"""Independent benchmark ownership must preserve execution safety and evidence."""
import importlib.util
from pathlib import Path

import pytest

from harness.web_fetch.reference_inputs import read_dataset_bytes, require_mutable_inputs
from harness.web_fetch.reference_inputs import benchmark_input_path
from lexbench_webfetch.inputs import definition_path

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative", [
    "evaluation/dataset.json",
    "evaluation/popular-sites/dataset.json",
    "evaluation/dynamic-content-100-moli-webfetch/dataset.json",
    "evaluation/risk-control/dataset.json",
    "evaluation/risk-control/current/dataset.json",
])
def test_relocated_frozen_inputs_remain_write_protected(relative):
    with pytest.raises(ValueError, match="immutable"):
        require_mutable_inputs(ROOT / relative)


def test_new_candidate_inputs_remain_writable(tmp_path):
    require_mutable_inputs(tmp_path / "candidate/dataset.json")


@pytest.mark.parametrize("symlink_checkout", [False, True])
def test_foreign_checkout_binding_works_outside_the_installation(tmp_path, monkeypatch, symlink_checkout):
    checkout = tmp_path / "checkout"
    (checkout / "evaluation").mkdir(parents=True)
    (checkout / "evaluation/lexbench-webfetch.json").write_bytes(
        (ROOT / "evaluation/lexbench-webfetch.json").read_bytes())
    if symlink_checkout:
        alias = tmp_path / "checkout-alias"
        alias.symlink_to(checkout, target_is_directory=True)
        checkout = alias
    monkeypatch.chdir(tmp_path)
    logical = checkout / "evaluation/dataset.json"
    assert benchmark_input_path(logical) == definition_path("evaluation/source-inputs.json").parent / "dataset.json"
    with pytest.raises(ValueError, match="immutable"):
        require_mutable_inputs(logical)


def test_unbound_input_with_the_same_suffix_is_not_a_benchmark_alias(tmp_path):
    candidate = tmp_path / "custom/evaluation/dataset.json"
    assert benchmark_input_path(candidate) == candidate
    require_mutable_inputs(candidate)


def test_harness_does_not_ship_a_second_evaluator():
    for module in ("scoring", "metrics", "risk_control_dataset", "stable_tasks",
                   "body_coverage", "composite_tasks", "dynamic_lists"):
        assert importlib.util.find_spec("harness.web_fetch." + module) is None


def test_frozen_definitions_have_one_installed_owner():
    import hashlib
    import json

    binding = json.loads((ROOT / "evaluation/lexbench-webfetch.json").read_text())
    import tomllib
    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert not any(value.startswith("lexbench-webfetch @") for value in dependencies)
    from harness.benchmarks.pinning import validate_binding
    assert validate_binding(ROOT) == binding["revision"]
    for relative, expected in binding["definition_files"].items():
        assert not (ROOT / relative).exists(), relative
        assert hashlib.sha256(definition_path(relative).read_bytes()).hexdigest() == expected


def test_historical_read_uses_the_original_frozen_bytes(historical_dataset):
    import hashlib
    import json

    manifest = json.loads(definition_path("evaluation/source-inputs.json").read_text())
    assert hashlib.sha256(read_dataset_bytes(ROOT / "evaluation/dataset.json")).hexdigest() == manifest["dataset_sha256"]


@pytest.mark.parametrize("spelling", ["evaluation/dataset.json",
    "evaluation/risk-control/../dataset.json", "evaluation/../evaluation/dataset.json", "../checkout/evaluation/dataset.json"])
@pytest.mark.parametrize("linked_checkout", [False, True])
def test_equivalent_historical_paths_cannot_read_or_modify_shadow_inputs(
        tmp_path, monkeypatch, spelling, linked_checkout):
    import json
    from harness.web_fetch.reference_inputs import load_manifest

    checkout = tmp_path / "real/checkout"
    (checkout / "evaluation/risk-control").mkdir(parents=True)
    (checkout / "evaluation/lexbench-webfetch.json").write_bytes(
        (ROOT / "evaluation/lexbench-webfetch.json").read_bytes())
    (checkout / "evaluation/dataset.json").write_text('{"id":"shadow","cases":[]}')
    if linked_checkout:
        alias = tmp_path / "linked-checkout"
        alias.symlink_to(checkout, target_is_directory=True)
        checkout = alias
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "missing-inputs"))
    logical = checkout / spelling
    installed = definition_path("evaluation/source-inputs.json")
    assert benchmark_input_path(logical) == installed.parent / "dataset.json"
    assert load_manifest(logical.parent / "source-inputs.json") == json.loads(installed.read_text())
    with pytest.raises(ValueError, match="immutable"):
        require_mutable_inputs(logical)
    with pytest.raises(FileNotFoundError, match="Reference inputs are not installed"):
        read_dataset_bytes(logical)


@pytest.mark.parametrize("spelling", ["evaluation/dataset.json",
    "evaluation/risk-control/../dataset.json", "../checkout/evaluation/dataset.json"])
@pytest.mark.parametrize("linked_checkout", [False, True])
def test_equivalent_historical_paths_still_reject_a_symlinked_input(tmp_path, spelling, linked_checkout):
    checkout = tmp_path / "real/checkout"
    (checkout / "evaluation/risk-control").mkdir(parents=True)
    (checkout / "evaluation/lexbench-webfetch.json").write_bytes(
        (ROOT / "evaluation/lexbench-webfetch.json").read_bytes())
    source = tmp_path / "candidate.json"
    source.write_text('{"cases":[]}')
    (checkout / "evaluation/dataset.json").symlink_to(source)
    if linked_checkout:
        alias = tmp_path / "linked-checkout"
        alias.symlink_to(checkout, target_is_directory=True)
        checkout = alias
    with pytest.raises(ValueError, match="symlinked"):
        benchmark_input_path(checkout / spelling)


def test_linked_input_directory_cannot_hide_its_checkout_binding(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    (checkout / "evaluation").mkdir(parents=True)
    (checkout / "evaluation/lexbench-webfetch.json").write_bytes(
        (ROOT / "evaluation/lexbench-webfetch.json").read_bytes())
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "dataset.json").write_text('{"id":"shadow","cases":[]}')
    (checkout / "evaluation/popular-sites").symlink_to(candidate, target_is_directory=True)
    logical = checkout / "evaluation/popular-sites/dataset.json"
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "missing-inputs"))
    assert benchmark_input_path(logical) == definition_path("evaluation/popular-sites/source-inputs.json").parent / "dataset.json"
    with pytest.raises(ValueError, match="immutable"):
        require_mutable_inputs(logical)
    with pytest.raises(FileNotFoundError, match="Reference inputs are not installed"):
        read_dataset_bytes(logical)
