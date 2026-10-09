from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena import release
from harness.trajectory_replay.public_webarena.publication import (
    build,
    check_report_bundle,
    stage_report,
)
from harness.trajectory_replay.public_webarena.report import METRICS
from tests.public_webarena_fixture import write_report_fixture


def test_stage_builds_report_without_mutating_source_evidence(tmp_path, monkeypatch):
    source, profile = write_report_fixture(tmp_path)
    before = (source / "run/summary.json").read_bytes()

    staged = tmp_path / "staged"
    stage_report(source, staged)

    assert (staged / "reports/webarena-trajectory-replay-latest.html").is_file()
    assert (staged / "reports/webarena-trajectory-replay-latest-results.html").is_file()
    method = staged / "reports/webarena-trajectory-replay-latest-method.html"
    assert method.is_file()
    assert "核验结论" in method.read_text(encoding="utf-8")
    assert check_report_bundle(staged) == {
        "pages": 6,
        "metrics": sum(len(metrics) for _, metrics in METRICS),
        "ratio_modules": 0,
    }
    assert (source / "run/summary.json").read_bytes() == before

    authority = json.loads(profile.read_text())
    archive, receipt = tmp_path / "report.zip", tmp_path / "attestation.json"
    result = build(staged, archive, receipt, authority=profile)
    assert result["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("index.html") == bundle.read("webarena-trajectory-replay-latest.html")
    assert result["results"] == {
        engine: {"tasks": 1, "passes": 1} for engine in ("chromium", "moli")
    }
    monkeypatch.setattr(sys, "argv", ["release", "--formal-root", str(source)])
    release.main(tmp_path)
    assert (tmp_path / "publish/webarena-fixed-trajectory.zip").is_file()
    assert json.loads(profile.read_text()) == authority
    authority["suite_sha256"] = "wrong"
    profile.write_text(json.dumps(authority))
    rejected = tmp_path / "rejected.zip"
    with pytest.raises(ValueError, match="current report authority mismatch: suite hash"):
        build(staged, rejected, receipt, authority=profile)
    assert not rejected.exists()

    homepage = staged / "reports/webarena-trajectory-replay-latest.html"
    broken = homepage.read_text(encoding="utf-8").replace(
        'data-preference="neutral">', 'data-preference="neutral"><b class="better">wrong</b>', 1
    )
    homepage.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="neutral metric must not declare a winner"):
        check_report_bundle(staged)


def test_profile_is_updated_only_with_verified_publication_values(tmp_path):
    profile = tmp_path / "current-report.json"
    profile.write_text(
        json.dumps({"publication": {"version_id": "old", "submitted_files_verified": 1}})
    )
    from harness.reporting.publication import bind_publication_files

    attestation = tmp_path / "attestation.json"
    attestation.write_text("{}")
    bind_publication_files(
        profile,
        attestation,
        tmp_path / "binding.json",
        publication={"version_id": "ver_new", "submitted_files_verified": 71},
        schema="public_webarena_binding/1",
    )
    assert (
        json.loads(attestation.read_text())["profile_sha256"]
        == hashlib.sha256(profile.read_bytes()).hexdigest()
    )

    current = json.loads(profile.read_text())
    assert current["publication"] == {
        "version_id": "ver_new",
        "submitted_files_verified": 71,
    }


def test_verification_command_uses_required_slug_option(tmp_path):
    command = release._verification_command(
        {"site_slug": "site", "base_url": "https://hub.example"},
        tmp_path / "report.zip",
        tmp_path,
    )
    assert command[2:4] == ["--slug", "site"]
    assert command[-2:] == ["--expected-archive", str(tmp_path / "report.zip")]


def publication_fixture(root):
    from harness.trajectory_replay.public_webarena import publication

    source, profile = write_report_fixture(root)
    document = json.loads(profile.read_text())
    document["publication"] = {
        "base_url": "https://reports.example",
        "site_slug": "site-one",
        "version_id": "ver_old",
        "share_url": "https://reports.example/v/token",
    }
    profile.write_text(json.dumps(document))
    staged = root / "staged"
    publication.stage_report(source, staged)
    archive, proof = root / "report.zip", root / "proof.json"
    publication.build(staged, archive, proof, authority=profile)
    receipt = root / "receipt.json"
    receipt.write_text(
        json.dumps(
            {"slug": "site-one", "versionId": "ver_old", "base_url": "https://reports.example"}
        )
    )
    return source, profile, staged, archive, proof, receipt, root / "operation.json"


@pytest.mark.parametrize("failure", ["upload-response", "share"])
def test_public_replay_submission_recovers_without_second_activation(tmp_path, failure):
    from harness.trajectory_replay.public_webarena.publication import publish_current
    from tests.test_webfetch_report_publish import Service

    source, profile, staged, archive, proof, receipt, journal = publication_fixture(tmp_path)
    service = Service(failure)
    before = profile.read_bytes()
    with pytest.raises(OSError, match="simulated"):
        publish_current(profile, staged, archive, proof, receipt, journal, transport=service)
    result = publish_current(profile, staged, archive, proof, receipt, journal, transport=service)
    assert result["slug"] == "site-one" and result["version"] == "ver_new"
    assert len(service.uploads) == 1
    assert not journal.exists()
    assert "private-secret" not in receipt.read_text()
    assert profile.read_bytes() == before


@pytest.mark.parametrize("damage", ["site", "share", "proof", "package"])
def test_public_replay_preflight_rejects_wrong_authority_or_package_before_requests(
    tmp_path, damage
):
    from harness.trajectory_replay.public_webarena.publication import publish_current
    from tests.test_webfetch_report_publish import Service

    source, profile, staged, archive, proof, receipt, journal = publication_fixture(tmp_path)
    if damage == "site":
        document = json.loads(receipt.read_text())
        document["slug"] = "other-site"
        receipt.write_text(json.dumps(document))
    elif damage == 'share':
        document = json.loads(receipt.read_text())
        document['share_url'] = 'https://reports.example/v/other-report'
        receipt.write_text(json.dumps(document))
    elif damage == "proof":
        document = json.loads(proof.read_text())
        document["profile_sha256"] = "0" * 64
        proof.write_text(json.dumps(document))
    else:
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr("undeclared.txt", "not part of the bound report")
        document = json.loads(proof.read_text())
        document["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        proof.write_text(json.dumps(document))
    service = Service()
    with pytest.raises(ValueError):
        publish_current(profile, staged, archive, proof, receipt, journal, transport=service)
    assert service.requests == []


def test_release_retains_submission_when_export_verification_is_interrupted(tmp_path, monkeypatch):
    from harness.trajectory_replay.public_webarena import publication
    from tests.test_webfetch_report_publish import Service

    source, profile, staged, archive, proof, receipt, journal = publication_fixture(tmp_path)
    target = tmp_path / "publish/webarena-fixed-trajectory-site-receipt.json"
    target.parent.mkdir()
    target.write_bytes(receipt.read_bytes())
    service = Service()
    interrupted = [False]

    def command(args, root):
        if "--slug" in args:
            if not interrupted[0]:
                interrupted[0] = True
                raise RuntimeError("synthetic export verification interruption")
            return {"version": "ver_new", "files": json.loads(proof.read_text())["files"]}

        def get(name):
            return Path(args[args.index("--" + name) + 1])

        return publication.publish_current(
            get("profile"),
            get("formal-root"),
            get("archive"),
            get("attestation"),
            get("receipt"),
            get("journal"),
            transport=service,
        )

    monkeypatch.setattr(release, "_run_json", command)
    monkeypatch.setattr(sys, "argv", ["release", "--formal-root", str(source), "--publish"])
    with pytest.raises(RuntimeError, match="synthetic export"):
        release.main(tmp_path)
    assert json.loads(target.read_text())["submission_state"] == "submitted_unverified"
    assert json.loads(profile.read_text())["publication"]["version_id"] == "ver_old"
    release.main(tmp_path)
    assert len(service.uploads) == 1
    assert json.loads(target.read_text())["submission_state"] == "verified"
    assert json.loads(profile.read_text())["publication"]["version_id"] == "ver_new"
