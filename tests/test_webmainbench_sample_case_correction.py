"""A case revision retains complete calls and exposes only a complete new bundle."""

import copy
import json
import sys

import pytest

from harness.web_fetch import webmainbench_case_correction as correction
from tests.webmainbench_case_fixture import correction_fixture, write_json, write_rows


def apply_fixture(fixture):
    return correction.apply(*fixture[:7], profile=fixture[7])


@pytest.fixture
def retained(tmp_path, monkeypatch):
    fixture = correction_fixture(tmp_path / "fixture")
    monkeypatch.setattr(correction, "DATASET_SHA256", correction.digest(fixture[5]))
    return fixture


def test_revision_materializes_every_retry_and_excludes_unbound_files(retained):
    receipt = apply_fixture(retained)
    source, bindings, output, case, providers, *_ = retained
    provider = providers[0]
    shard = output / provider
    assert receipt["effects"][provider]["new_physical_calls"] == 2
    for call in correction.read_jsonl(shard / "physical-attempts.jsonl"):
        assert correction.digest(shard / call["physical_artifact_path"]) == call["output_sha256"]
    assert not list(output.rglob("private-worker.txt"))
    assert not list(output.rglob("unbound.txt"))
    assert (source / provider / "private-worker.txt").is_file()
    assert correction.read_jsonl(shard / "scored-results.jsonl")[1:] == correction.read_jsonl(source / provider / "scored-results.jsonl")[1:]
    for name in ("kitesurf", "lightpanda", "obscura", "moli"):
        for filename in ("scored-results.jsonl", "results.jsonl", "physical-attempts.jsonl",
                         "score-summary.json", "execution.json", "dataset.json"):
            assert (output / name / filename).read_bytes() == (source / name / filename).read_bytes()


def test_profile_can_declare_an_additional_method(tmp_path, monkeypatch):
    fixture = correction_fixture(tmp_path / "six", providers=("moli", "chromium", "new-engine"))
    monkeypatch.setattr(correction, "DATASET_SHA256", correction.digest(fixture[5]))
    apply_fixture(fixture)
    assert {p.name for p in fixture[2].iterdir() if p.is_dir()} == {"moli", "chromium", "new-engine"}


@pytest.mark.parametrize("damage", ["metrics", "physical", "retry", "missing-method", "duplicate-case", "identity", "raw-result"])
def test_invalid_revision_leaves_no_partial_output(retained, damage):
    source, bindings, output, _, providers, _, _, _ = retained
    replacement = bindings[providers[0]]
    rows = correction.read_jsonl(replacement / "scored-results.jsonl")
    if damage == "metrics":
        del rows[0]["metrics"]["text_edit"]
        write_rows(replacement / "scored-results.jsonl", rows)
    elif damage == "physical":
        (replacement / f"artifacts/{providers[0]}/case-0-p01.txt").write_text("corrupt")
    elif damage == "retry":
        calls = correction.read_jsonl(replacement / "physical-attempts.jsonl")
        calls[0]["physical_attempt"] = 2
        write_rows(replacement / "physical-attempts.jsonl", calls)
    elif damage == "missing-method":
        (source / "moli").rename(source / "unknown-method")
    elif damage == "duplicate-case":
        old = correction.read_jsonl(source / "moli/scored-results.jsonl")
        old[-1] = copy.deepcopy(old[0])
        write_rows(source / "moli/scored-results.jsonl", old)
    elif damage == "identity":
        execution = json.loads((replacement / "execution.json").read_text())
        execution["implementation_identity"]["converter"] = "changed"
        write_json(replacement / "execution.json", execution)
    else:
        raw = correction.read_jsonl(replacement / "results.jsonl")
        raw[0]["elapsed_ms"] = 999
        write_rows(replacement / "results.jsonl", raw)
    with pytest.raises((ValueError, FileNotFoundError)):
        apply_fixture(retained)
    assert not output.exists()


def test_symlinked_retry_is_rejected(retained):
    replacement = next(iter(retained[1].values()))
    retry = next(replacement.glob("artifacts/*/*p01.txt"))
    data = retry.read_bytes()
    retry.unlink()
    target = replacement / "external.txt"
    target.write_bytes(data)
    retry.symlink_to(target)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        apply_fixture(retained)
    assert not retained[2].exists()


@pytest.mark.parametrize("document", ["profile", "sample", "dataset"])
def test_frozen_input_changes_are_rejected(retained, document):
    path = retained[{"dataset": 5, "sample": 6, "profile": 7}[document]]
    if document == "profile":
        value = json.loads(path.read_text())
        value["providers"].append("missing")
        write_json(path, value)
    else:
        path.write_text(path.read_text() + " ")
    with pytest.raises((ValueError, FileNotFoundError)):
        apply_fixture(retained)
    assert not retained[2].exists()


@pytest.mark.parametrize("changed", ["source", "replacement", "profile"])
def test_mid_revision_input_change_leaves_no_output(retained, monkeypatch, changed):
    original = correction.copy_alias_artifacts
    done = False

    def mutate(*args, **kwargs):
        nonlocal done
        original(*args, **kwargs)
        if not done:
            done = True
            path = (retained[0] / "moli/private-worker.txt" if changed == "source"
                    else next(iter(retained[1].values())) / "private-worker.txt" if changed == "replacement"
                    else retained[7])
            path.write_text(path.read_text() + " ")

    monkeypatch.setattr(correction, "copy_alias_artifacts", mutate)
    with pytest.raises(ValueError, match="changed during revision"):
        apply_fixture(retained)
    assert not retained[2].exists()


def test_replacement_fixture_certificate_is_retained_and_bound(retained):
    replacement = next(iter(retained[1].values()))
    certificate = replacement / "fixture-ca.pem"
    certificate.write_text("synthetic fixture CA")
    expected = correction.digest(certificate)
    execution = json.loads((replacement / "execution.json").read_text())
    execution.update(fixture_ca_artifact=certificate.name,
                     network_isolation_probe={"fixture_ca_sha256": expected})
    write_json(replacement / "execution.json", execution)
    apply_fixture(retained)
    target = retained[2] / retained[4][0]
    run = json.loads((target / "execution.json").read_text())["correction_runs"][-1]
    assert correction.digest(target / run["fixture_ca_artifact"]) == expected
    assert run["fixture_ca_sha256"] == expected


def test_output_cannot_be_inside_replacement_evidence(retained):
    fixture = list(retained)
    fixture[2] = next(iter(fixture[1].values())) / "output"
    with pytest.raises(ValueError, match="outside replacement"):
        apply_fixture(fixture)
    assert not fixture[2].exists()


def test_command_accepts_repeated_bindings_and_infers_exact_scope(retained, tmp_path, monkeypatch, capsys):
    second = correction_fixture(tmp_path / "second", providers=("moli",))
    source, bindings, output, case, _, dataset, manifest, profile = retained
    bindings = {**bindings, **second[1]}
    argv = ["apply", "--source-root", str(source), "--output", str(output),
            "--case-id", case, "--dataset", str(dataset), "--sample-manifest", str(manifest),
            "--profile", str(profile)]
    for provider, root in bindings.items():
        argv += ["--correction", f"{provider}={root}"]
    monkeypatch.setattr(sys, "argv", argv)
    correction.main()
    assert set(json.loads(capsys.readouterr().out)["providers"]) == {"chromium", "moli"}
    receipt = json.loads((output / "fixture-correction.json").read_text())
    assert set(receipt["effects"]) == {"chromium", "moli"}


def test_existing_output_is_never_modified(retained):
    output = retained[2]
    output.mkdir()
    (output / "marker").write_text("preserve")
    with pytest.raises(ValueError, match="already exists"):
        apply_fixture(retained)
    assert [p.name for p in output.iterdir()] == ["marker"]
    assert (output / "marker").read_text() == "preserve"


def test_attempt_identity_cannot_collide_across_methods(retained):
    source = retained[0]
    first = correction.read_jsonl(source / "kitesurf/scored-results.jsonl")
    second = correction.read_jsonl(source / "moli/scored-results.jsonl")
    second[0]["attempt_id"] = first[0]["attempt_id"]
    write_rows(source / "moli/scored-results.jsonl", second)
    write_rows(source / "moli/results.jsonl", [{k: v for k, v in row.items() if k != "metrics"} for row in second])
    with pytest.raises(ValueError, match="identity is not unique"):
        apply_fixture(retained)
    assert not retained[2].exists()
