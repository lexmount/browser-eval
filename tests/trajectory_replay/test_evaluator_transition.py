from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.trajectory_replay import evaluator_transition
from harness.trajectory_replay.evaluator_transition import _old_evaluation_identity


@pytest.mark.parametrize(
    "changed", [None, "evaluation.json", "actual_response.json", "network.har"]
)
def test_transition_binds_files_consumed_by_evaluator(tmp_path: Path, monkeypatch, changed) -> None:
    for name, value in (
        ("evaluation.json", {"replay_assertions": []}),
        ("actual_response.json", {"answer": "saved answer"}),
        ("network.har", {"log": {"entries": []}}),
    ):
        (tmp_path / name).write_text(json.dumps(value))
    before = {
        name: (tmp_path / name).read_bytes()
        for name in ("evaluation.json", "actual_response.json", "network.har")
    }

    def evaluate_task(**kwargs):
        assert kwargs["agent_response"] == {"answer": "saved answer"}
        assert kwargs["network_trace"].read_bytes() == before["network.har"]
        if changed:
            (tmp_path / changed).write_text(json.dumps({"changed": True}))
        return SimpleNamespace(model_dump=lambda **kw: {"score": 1.0})

    benchmark = SimpleNamespace(get_task=lambda task_id: object(), evaluate_task=evaluate_task)
    monkeypatch.setattr(evaluator_transition, "validate_passing_evaluation", lambda *a, **kw: None)
    args = dict(
        task_id=7,
        expected_data_checksum="a" * 64,
        benchmark=benchmark,
        corrections=SimpleNamespace(records={}),
    )
    if changed:
        with pytest.raises(ValueError, match="evidence changed during evaluation"):
            evaluator_transition.reevaluate_pass(tmp_path, **args)
    else:
        current, binding = evaluator_transition.reevaluate_pass(tmp_path, **args)
        assert current["score"] == 1.0
        assert binding["actual_response_sha256"] == evaluator_transition.file_sha256(
            tmp_path / "actual_response.json"
        )
        assert all((tmp_path / name).read_bytes() == raw for name, raw in before.items())


def test_existing_transition_output_is_rejected_before_evaluation(
    tmp_path: Path, monkeypatch
) -> None:
    from harness.trajectory_replay import webarena_runtime_source

    def unexpected_evaluation():
        pytest.fail("occupied output must be rejected before runtime validation")

    monkeypatch.setattr(
        webarena_runtime_source, "verify_webarena_runtime_source", unexpected_evaluation
    )
    output = tmp_path / "receipt.json"
    output.write_bytes(b"preserve existing receipt")
    with pytest.raises(ValueError, match="output already exists"):
        evaluator_transition.main(
            [
                "--transition",
                str(tmp_path / "missing-transition.json"),
                "--scope",
                str(tmp_path / "missing-scope.json"),
                "--site-map",
                str(tmp_path / "missing-sites.json"),
                "--run",
                str(tmp_path / "missing-run"),
                "--output",
                str(output),
            ]
        )
    assert output.read_bytes() == b"preserve existing receipt"


@pytest.mark.parametrize(
    "changed", [None, "scope.json", "sites.json", "inputs.json", "progress.jsonl"]
)
def test_transition_command_freezes_batch_inputs(tmp_path: Path, monkeypatch, changed) -> None:
    from harness.trajectory_replay import (
        catalog_contracts,
        remote_runtime,
        webarena_corrections,
        webarena_runtime_source,
    )

    transition = _transition()
    run = tmp_path / "run"
    task = run / "task-7"
    task.mkdir(parents=True)
    paths = {
        "transition.json": tmp_path / "transition.json",
        "scope.json": tmp_path / "scope.json",
        "sites.json": tmp_path / "sites.json",
        "inputs.json": run / "inputs.json",
        "progress.jsonl": run / "progress.jsonl",
    }
    for name, value in (
        ("transition.json", transition),
        ("scope.json", {"official_dataset_sha256": "a" * 64}),
        ("sites.json", {"sites": {}}),
        ("inputs.json", {}),
        ("progress.jsonl", {"task_id": 7, "status": "pass"}),
    ):
        paths[name].write_text(json.dumps(value) + "\n")
    source = {"evaluator_checksum": "b" * 64, "data_checksum": "c" * 64}
    old = {
        "webarena_verified_evaluator_checksum": source["evaluator_checksum"],
        "webarena_verified_data_checksum": source["data_checksum"],
    }
    for name, value in (
        ("evaluation.json", old),
        ("actual_response.json", {}),
        ("network.har", {}),
    ):
        (task / name).write_text(json.dumps(value))
    before = {path: path.read_bytes() for path in paths.values()}
    monkeypatch.setattr(webarena_runtime_source, "verify_webarena_runtime_source", lambda: None)
    monkeypatch.setattr(remote_runtime, "remote_benchmark", lambda sites: object())
    monkeypatch.setattr(webarena_corrections, "load_corrections", lambda: object())
    monkeypatch.setattr(evaluator_transition, "compatible_source", lambda *a, **kw: source)
    monkeypatch.setattr(
        catalog_contracts,
        "verify_physical_evidence",
        lambda directory, row: task / "evaluation.json",
    )

    def reevaluate(directory, **kwargs):
        if changed:
            paths[changed].write_text("changed input")
        return {}, {
            "task_id": 7,
            "old_evaluation_sha256": evaluator_transition.file_sha256(task / "evaluation.json"),
            "actual_response_sha256": evaluator_transition.file_sha256(
                task / "actual_response.json"
            ),
            "network_har_sha256": evaluator_transition.file_sha256(task / "network.har"),
            "current_evaluation_sha256": "d" * 64,
        }

    monkeypatch.setattr(evaluator_transition, "reevaluate_pass", reevaluate)
    output = tmp_path / "receipt.json"
    args = [
        "--transition",
        str(paths["transition.json"]),
        "--scope",
        str(paths["scope.json"]),
        "--site-map",
        str(paths["sites.json"]),
        "--run",
        str(run),
        "--output",
        str(output),
    ]
    if changed:
        with pytest.raises(ValueError, match="inputs changed during receipt build"):
            evaluator_transition.main(args)
        assert not output.exists()
    else:
        evaluator_transition.main(args)
        assert json.loads(output.read_bytes())["row_count"] == 1
        assert all(path.read_bytes() == raw for path, raw in before.items())


def test_source_pass_allows_a_superseded_correction_bound_by_transition() -> None:
    source = {
        "evaluator_checksum": "a" * 64,
        "data_checksum": "b" * 64,
    }
    evaluator_transition.validate_source_pass(
        {
            "task_id": 566,
            "score": 1.0,
            "status": "success",
            "webarena_verified_evaluator_checksum": source["evaluator_checksum"],
            "webarena_verified_data_checksum": source["data_checksum"],
            "contract_correction_id": "superseded-upstream-fix",
            "corrected_task_sha256": "c" * 64,
            "evaluators_results": [{"score": 1.0, "status": "success"}],
        },
        task_id=566,
        source=source,
    )


def test_source_pass_rejects_a_failed_evaluator_row() -> None:
    source = {
        "evaluator_checksum": "a" * 64,
        "data_checksum": "b" * 64,
    }
    with pytest.raises(ValueError, match="source pass"):
        evaluator_transition.validate_source_pass(
            {
                "task_id": 7,
                "score": 1.0,
                "status": "success",
                "webarena_verified_evaluator_checksum": source["evaluator_checksum"],
                "webarena_verified_data_checksum": source["data_checksum"],
                "evaluators_results": [{"score": 0.0, "status": "failure"}],
            },
            task_id=7,
            source=source,
        )


def _transition():
    return evaluator_transition.load_transition(
        Path("evaluation/trajectory-replay/webarena-verified-full/evaluator-transition.json")
    )


def _receipt(transition):
    return {
        "schema": evaluator_transition.RECEIPT_SCHEMA,
        "transition_sha256": evaluator_transition.json_sha256(transition),
        "from_revision": transition["from_revision"],
        "compatible_from_revisions": transition["compatible_from_revisions"],
        "compatible_sources": transition["compatible_sources"],
        "from_evaluator_checksum": transition["from_evaluator_checksum"],
        "from_data_checksum": transition["from_data_checksum"],
        "to_revision": transition["to_revision"],
        "to_evaluator_checksum": transition["to_evaluator_checksum"],
        "to_data_checksum": transition["to_data_checksum"],
        "row_count": 1,
        "excluded_old_evaluations": [],
        "rows": [
            {
                "task_id": 7,
                "old_evaluation_sha256": "1" * 64,
                "actual_response_sha256": "2" * 64,
                "network_har_sha256": "3" * 64,
                "current_evaluation_sha256": "4" * 64,
                "run_inputs_sha256": "5" * 64,
                "source": transition["compatible_sources"][0],
            }
        ],
    }


def test_transition_authority_is_bound_to_the_installed_fork_revision():
    transition = _transition()

    assert transition["from_revision"] != transition["to_revision"]
    assert transition["to_evaluator_checksum"] == (
        evaluator_transition.compute_evaluator_checksum()
    )


def test_transition_resolves_revision_and_legacy_evaluator_sources():
    transition = _transition()

    revision = evaluator_transition.compatible_source(
        transition,
        revision="4c3b14e5cda91889f5ee883c8661bccd1d96c1af",
        evaluator_fixes_sha256=None,
    )
    legacy = evaluator_transition.compatible_source(
        transition,
        revision=None,
        evaluator_fixes_sha256=("58b451900c239ed6fb9a9a99633409bc56190676b9f2273ed45c472e8eb1ff08"),
    )

    assert revision["evaluator_checksum"] == (
        "7f6b1efd6ae2f88643f623a8ab4b1aeb107c0fa912b59f5ffd753de20337a226"
    )
    assert legacy["data_checksum"] == (
        "7ab0ef7e61af98efc5e16ba2b4303ddc0b8f5b66b5c88c58cb76e89e9235b57a"
    )


def test_transition_exclusion_cli_binds_evaluation_and_run():
    assert _old_evaluation_identity(f"7/{'1' * 64}/{'2' * 64}") == (
        7,
        "1" * 64,
        "2" * 64,
    )
    with pytest.raises(
        argparse.ArgumentTypeError,
        match="EVALUATION_SHA256/RUN_INPUTS_SHA256",
    ):
        _old_evaluation_identity(f"7/{'1' * 64}")


def test_transition_receipt_binds_each_old_evaluation_once(tmp_path):
    transition = _transition()
    receipt = _receipt(transition)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))

    indexed, excluded = evaluator_transition.load_receipt(path, transition)

    assert indexed[(7, "1" * 64, "5" * 64)]["current_evaluation_sha256"] == "4" * 64
    assert excluded == set()


def test_transition_receipt_excludes_only_the_bound_old_evaluation(tmp_path):
    transition = _transition()
    receipt = _receipt(transition)
    receipt["excluded_old_evaluations"] = [
        {
            "task_id": 7,
            "old_evaluation_sha256": "1" * 64,
            "run_inputs_sha256": "6" * 64,
        }
    ]
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))

    indexed, excluded = evaluator_transition.load_receipt(path, transition)

    assert set(indexed) == {(7, "1" * 64, "5" * 64)}
    assert excluded == {(7, "1" * 64, "6" * 64)}


def test_transition_receipt_distinguishes_identical_evaluations_from_different_runs(
    tmp_path,
):
    transition = _transition()
    receipt = _receipt(transition)
    replacement = {
        **receipt["rows"][0],
        "run_inputs_sha256": "6" * 64,
    }
    receipt["rows"].append(replacement)
    receipt["row_count"] = 2
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))

    indexed, excluded = evaluator_transition.load_receipt(path, transition)

    assert set(indexed) == {
        (7, "1" * 64, "5" * 64),
        (7, "1" * 64, "6" * 64),
    }
    assert excluded == set()


@pytest.mark.parametrize("change", ["extra_row", "wrong_transition", "wrong_count"])
def test_transition_receipt_rejects_unbound_or_duplicate_evidence(tmp_path, change):
    transition = _transition()
    receipt = _receipt(transition)
    if change == "extra_row":
        receipt["rows"].append(dict(receipt["rows"][0]))
        receipt["row_count"] = 2
    elif change == "wrong_transition":
        receipt["transition_sha256"] = "0" * 64
    else:
        receipt["row_count"] = 2
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))

    with pytest.raises(ValueError, match="transition receipt"):
        evaluator_transition.load_receipt(path, transition)
