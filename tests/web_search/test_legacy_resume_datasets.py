"""Historical Git-pinned collections need an explicit, input-preserving repair."""

import copy
import json
from pathlib import Path

import pytest

from harness.benchmarks import data
from harness.web_search import runner
from tests.web_search.fixtures import execution_profile

# The dependency recorded in the pre-migration Browser Eval pyproject and uv.lock.
LEGACY_PIN = "f07a2e2ddf1c1c511cb3944659534301c86ff9c9"
GIT = "https://github.com/lexmount/lexbench-websearch.git"


def _dependency_files(output, contract, pin=LEGACY_PIN, lock_pin=None):
    frozen = output / "implementation"
    project = f'[project]\ndependencies = ["lexbench-websearch @ git+{GIT}@{pin}"]\n'
    lock_pin = pin if lock_pin is None else lock_pin
    lock = (
        'version = 1\n[[package]]\nname = "lexbench-websearch"\n'
        f'source = {{ git = "{GIT}?rev={lock_pin}#{lock_pin}" }}\n'
    )
    for name, text in (("pyproject.toml", project), ("uv.lock", lock)):
        (frozen / name).write_text(text)
        contract["implementation"][name] = runner.digest(text.encode())


@pytest.fixture
def legacy_run(tmp_path, monkeypatch):
    """Reproduce the pre-PR contract layout, dependency pins and frozen bundle."""
    profile = execution_profile()
    profile["cases"] = profile["cases"][:1]
    profile["protocol"].update(rounds=1, interval_s=0)
    profile["protocol"].pop("reference_scope", None)
    profile.pop("reference_checks", None)
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(profile))
    output = tmp_path / "run"
    monkeypatch.setattr(runner, "load_runtime_env", lambda: None)
    for method in profile["methods"]:
        monkeypatch.setenv(method["key_env"], "fixture")

    sent = 0

    def http(url, body=None, header=None, timeout=20):
        nonlocal sent
        if body is not None:
            sent += 1
            if sent > 1:
                raise KeyboardInterrupt
        receipt = {
            "http_status": 200,
            "elapsed_ms": 1,
            "transport_exit": 0,
            "transport_error": "",
        }
        return receipt, b'{"results":[]}' if body is not None else b"reference " * 20

    monkeypatch.setattr(runner, "http_call", http)
    with pytest.raises(KeyboardInterrupt):
        runner.run(source, output, certified=True)
    contract = json.loads((output / "run-contract.json").read_bytes())
    contract.pop("installed_benchmark_datasets")
    old_prefix, new_prefix = "lexbench_websearch/", "dependencies/lexbench_websearch/"
    for name in list(contract["implementation"]):
        if name.startswith(old_prefix):
            renamed = new_prefix + name.removeprefix(old_prefix)
            contract["implementation"][renamed] = contract["implementation"].pop(name)
            destination = output / "implementation" / renamed
            destination.parent.mkdir(parents=True, exist_ok=True)
            (output / "implementation" / name).rename(destination)
    contract["implementation"].pop("harness/benchmarks/data.py")
    (output / "implementation/harness/benchmarks/data.py").unlink()
    _dependency_files(output, contract)
    (output / "run-contract.json").write_text(json.dumps(contract))
    revised = copy.deepcopy(profile)
    revised["methods"][1]["endpoint"] = "https://replacement.invalid/search"
    replacement = tmp_path / "revised.json"
    replacement.write_text(json.dumps(revised))
    proof = tmp_path / "proof.txt"
    proof.write_text("The native request endpoint changed; the task population stays fixed.")
    revision = tmp_path / "revision.json"
    revision.write_text(
        json.dumps(
            {
                "schema": "web-search-execution-revision/1",
                "root_cause": "Native endpoint changed",
                "superseded_methods": [revised["methods"][1]["id"]],
                "previous_profile_sha256": runner.digest(source.read_bytes()),
                "profile_sha256": runner.digest(replacement.read_bytes()),
                "evidence": [{"path": proof.name, "sha256": runner.digest(proof.read_bytes())}],
            }
        )
    )
    return output, contract, replacement, revision


def test_known_legacy_execution_revision_upgrades_once_and_can_resume(legacy_run):
    output, contract, profile, revision = legacy_run
    original = (output / "run-contract.json").read_bytes()
    assert runner.validate_resume_datasets(contract, output) == data.dependencies("websearch")
    with pytest.raises(ValueError, match="same live collection implementation"):
        runner.validate_resume_implementation(output, contract)
    original_rows = runner.read_completed_records(output / "results.jsonl")
    assert len(original_rows) == 1
    original_call = output / original_rows[0]["evidence"]["path"]
    original_call_bytes = original_call.read_bytes()
    assert runner.prepare_execution_revision(profile, output, revision) == original_rows
    assert original_call.read_bytes() == original_call_bytes
    assert (output / "resume-1/previous-run-contract.json").read_bytes() == original
    current = json.loads((output / "run-contract.json").read_bytes())
    assert current["installed_benchmark_datasets"] == data.dependencies("websearch")
    assert "lexbench_websearch/scoring.py" in current["implementation"]
    assert not any(name.startswith("dependencies/") for name in current["implementation"])
    runner.prepare_resume(profile, output, retain_results=True, validate_only=True)


@pytest.mark.parametrize(
    "change", ["unknown-pin", "conflicting-pin", "unbound-source", "tampered-bundle"]
)
def test_legacy_repair_rejects_unproven_sources_before_mutations(legacy_run, change):
    output, contract, profile, revision = legacy_run
    if change == "unknown-pin":
        _dependency_files(output, contract, pin="f" * 40)
    elif change == "conflicting-pin":
        _dependency_files(output, contract, lock_pin="e" * 40)
    elif change == "unbound-source":
        contract["implementation"].pop("dependencies/lexbench_websearch/inputs.py")
    else:
        (output / "implementation/dependencies/lexbench_websearch/scoring.py").write_text("changed")
    (output / "run-contract.json").write_text(json.dumps(contract))
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="dataset|dependency|hash mismatch"):
        runner.prepare_execution_revision(profile, output, revision)
    assert {
        p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()
    } == before
    assert not list(output.glob("resume-*"))


@pytest.mark.parametrize(
    "change",
    ["metadata-only", "receipt-only", "case", "registry", "rubric", "factual", "factual-case"],
)
def test_legacy_bindings_compare_runtime_inputs_not_publication_metadata(
    tmp_path, monkeypatch, change
):
    lock = json.loads(Path(data.__file__).with_name("data-lock.json").read_bytes())
    current = lock["datasets"]["websearch"]
    if change == "metadata-only":
        current["revision"] = "d" * 40
        current["files"]["README.md"] = {"sha256": "a" * 64, "size": 1}
    elif change == "receipt-only":
        current["files"]["definitions/scoring-revisions.json"]["sha256"] = "a" * 64
    elif change == "case":
        current["files"]["definitions/datasets/scenarios-v2.json"]["sha256"] = "a" * 64
    elif change == "rubric":
        current["files"]["definitions/preview-rubric-v2.txt"]["sha256"] = "a" * 64
    elif change == "registry":
        current["files"]["definitions/registry.json"]["sha256"] = "a" * 64
    else:
        relative = (
            "definitions/factual-source.json"
            if change == "factual"
            else "definitions/datasets/factual-v1.json"
        )
        lock["datasets"]["simpleqa-verified"]["files"][relative]["sha256"] = "a" * 64
    (tmp_path / "data-lock.json").write_text(json.dumps(lock))
    monkeypatch.setattr(data, "__file__", str(tmp_path / "data.py"))
    if change in {"metadata-only", "receipt-only"}:
        assert data.legacy_dependencies("websearch", LEGACY_PIN) == data.dependencies("websearch")
    else:
        with pytest.raises(ValueError, match="dataset"):
            data.legacy_dependencies("websearch", LEGACY_PIN)


def test_unknown_legacy_git_pin_has_no_compatibility_exemption():
    with pytest.raises(ValueError, match="dataset"):
        data.legacy_dependencies("websearch", "f" * 40)


@pytest.mark.parametrize("relative", ["datasets/factual-v1.json", "factual-source.json"])
def test_legacy_source_bytes_cannot_be_overwritten_by_current_composition(
    tmp_path, monkeypatch, relative
):
    lock = json.loads(Path(data.__file__).with_name("data-lock.json").read_bytes())
    current = lock["datasets"]["websearch"]
    old = next(item for item in current["history"] if item["source_revision"] == LEGACY_PIN)
    old["datasets"] = copy.deepcopy(current["datasets"])
    old["dataset_sources"] = dict(current["dataset_sources"])
    old["files"]["definitions/" + relative]["sha256"] = "a" * 64
    if relative.startswith("datasets/"):
        old["datasets"]["factual-v1"]["sha256"] = "a" * 64
    (tmp_path / "data-lock.json").write_text(json.dumps(lock))
    monkeypatch.setattr(data, "__file__", str(tmp_path / "data.py"))
    with pytest.raises(ValueError, match="dataset"):
        data.legacy_dependencies("websearch", LEGACY_PIN)
