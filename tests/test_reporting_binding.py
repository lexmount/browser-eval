"""Both report tracks resume local binding without overwriting a newer authority."""

import hashlib
import json

import pytest

from harness.browser_use import report_publish as autonomous
from harness.reporting import publication
from harness.trajectory_replay import webarena_report_publish as replay


@pytest.mark.parametrize("track", ["browser-use", "replay"])
def test_binding_does_not_replace_a_concurrently_created_journal(tmp_path, monkeypatch, track):
    _, profile, proof, journal, bind = setup(tmp_path, monkeypatch, track)
    before = (profile.read_bytes(), proof.read_bytes())
    original = publication.atomic_create

    def concurrent_create(path, payload):
        path.write_bytes(b'{"another_transaction":true}')
        original(path, payload)

    monkeypatch.setattr(publication, "atomic_create", concurrent_create)
    with pytest.raises(FileExistsError):
        bind()
    assert (profile.read_bytes(), proof.read_bytes()) == before
    assert journal.read_bytes() == b'{"another_transaction":true}'


def setup(tmp_path, monkeypatch, track):
    owner = autonomous if track == "browser-use" else replay
    profile, proof, journal = [
        tmp_path / name for name in ("profile.json", "proof.json", "pending.json")
    ]
    profile.write_text(
        json.dumps(
            {"publication": {"base_url": "https://example.invalid", "expected_version": "ver_old"}}
        )
    )
    proof.write_text(
        json.dumps(
            {
                "archive_sha256": "frozen-package",
                "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
            }
        )
    )
    for key, path in [("PROFILE", profile), ("ATTESTATION", proof), ("BINDING_JOURNAL", journal)]:
        monkeypatch.setattr(owner, key, path, raising=False)

    def bind():
        if track == "browser-use":
            owner.bind_published_version(
                "ver_new", slug="site-1", share_url="https://example.invalid/v/token"
            )
        else:
            owner.bind_publication(
                json.loads(profile.read_text()),
                slug="site-1",
                share_url="https://example.invalid/v/token",
                version="ver_new",
            )

    return owner, profile, proof, journal, bind


@pytest.mark.parametrize("track", ["browser-use", "replay"])
@pytest.mark.parametrize("phase", ["profile", "proof"])
def test_interrupted_binding_resumes_complete_pair(tmp_path, monkeypatch, track, phase):
    owner, profile, proof, journal, bind = setup(tmp_path, monkeypatch, track)
    original = getattr(owner, "atomic_write", lambda path, data: path.write_bytes(data))

    def interrupt(path, data):
        if path == (profile if phase == "profile" else proof):
            raise OSError("simulated binding interruption")
        original(path, data)

    monkeypatch.setattr(owner, "atomic_write", interrupt, raising=False)
    with pytest.raises(OSError, match="binding interruption"):
        bind()
    assert journal.is_file()
    monkeypatch.setattr(owner, "atomic_write", original)
    owner.recover_publication_binding()
    assert not journal.exists()
    publication = json.loads(profile.read_text())["publication"]
    assert publication["expected_version"] == "ver_new"
    assert publication["site_slug"] == "site-1"
    assert (
        json.loads(proof.read_text())["profile_sha256"]
        == hashlib.sha256(profile.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("track", ["browser-use", "replay"])
@pytest.mark.parametrize("changed", ["profile", "proof"])
def test_recovery_refuses_changed_authority_without_overwriting_it(
    tmp_path, monkeypatch, track, changed
):
    owner, profile, proof, journal, bind = setup(tmp_path, monkeypatch, track)
    original = getattr(owner, "atomic_write", lambda path, data: path.write_bytes(data))

    def interrupt(path, data):
        if path == proof:
            raise OSError("simulated interruption")
        original(path, data)

    monkeypatch.setattr(owner, "atomic_write", interrupt, raising=False)
    with pytest.raises(OSError):
        bind()
    target = profile if changed == "profile" else proof
    target.write_text('{"newer_authority":true}')
    before = (profile.read_bytes(), proof.read_bytes(), journal.read_bytes())
    monkeypatch.setattr(owner, "atomic_write", original)
    with pytest.raises(ValueError, match="changed|differs"):
        owner.recover_publication_binding()
    assert (profile.read_bytes(), proof.read_bytes(), journal.read_bytes()) == before


@pytest.mark.parametrize("track", ["browser-use", "replay"])
def test_completed_binding_can_retry_failed_journal_cleanup(tmp_path, monkeypatch, track):
    from pathlib import Path

    owner, profile, proof, journal, bind = setup(tmp_path, monkeypatch, track)
    original = Path.unlink

    def interrupt(path, *args, **kwargs):
        if path == journal:
            raise OSError("simulated journal cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt)
    with pytest.raises(OSError, match="cleanup failure"):
        bind()
    before = (profile.read_bytes(), proof.read_bytes())
    assert journal.exists()
    monkeypatch.setattr(Path, "unlink", original)
    owner.recover_publication_binding()
    assert not journal.exists()
    assert (profile.read_bytes(), proof.read_bytes()) == before


@pytest.mark.parametrize("track", ["browser-use", "replay"])
def test_binding_journal_cannot_be_replayed_into_other_targets(tmp_path, monkeypatch, track):
    owner, profile, proof, journal, bind = setup(tmp_path, monkeypatch, track)
    original = owner.atomic_write

    def interrupt(path, data):
        if path == profile:
            raise OSError("simulated interruption")
        original(path, data)

    monkeypatch.setattr(owner, "atomic_write", interrupt)
    with pytest.raises(OSError):
        bind()
    other_profile, other_proof = tmp_path / "other-profile.json", tmp_path / "other-proof.json"
    other_profile.write_bytes(profile.read_bytes())
    other_proof.write_bytes(proof.read_bytes())
    monkeypatch.setattr(owner, "PROFILE", other_profile)
    monkeypatch.setattr(owner, "ATTESTATION", other_proof)
    monkeypatch.setattr(owner, "atomic_write", original)
    before = (other_profile.read_bytes(), other_proof.read_bytes())
    with pytest.raises(ValueError, match="targets differ"):
        owner.recover_publication_binding()
    assert (other_profile.read_bytes(), other_proof.read_bytes()) == before
    assert journal.exists()


@pytest.mark.parametrize("profile_installed", [False, True])
def test_legacy_replay_journal_only_finishes_the_already_installed_profile(
    tmp_path, monkeypatch, profile_installed
):
    owner, profile, proof, journal, _ = setup(tmp_path, monkeypatch, "replay")
    updated = {
        "publication": {"base_url": "https://example.invalid", "expected_version": "ver_new"}
    }
    payload = owner.json_bytes(updated)
    target_proof = json.loads(proof.read_bytes())
    target_proof["profile_sha256"] = hashlib.sha256(payload).hexdigest()
    journal.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_replay_publication_binding/1",
                "profile": updated,
                "attestation": target_proof,
            }
        )
    )
    if profile_installed:
        profile.write_bytes(payload)
        owner.recover_publication_binding()
        assert json.loads(proof.read_bytes()) == target_proof and not journal.exists()
    else:
        before = (profile.read_bytes(), proof.read_bytes(), journal.read_bytes())
        with pytest.raises(ValueError, match="legacy.*differs"):
            owner.recover_publication_binding()
        assert (profile.read_bytes(), proof.read_bytes(), journal.read_bytes()) == before
