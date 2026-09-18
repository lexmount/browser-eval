"""Fail-closed controls for the certified three-method replay aggregate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.aggregate_webarena_three_method_cohorts import _digest, _sha, aggregate


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _fixture(root: Path) -> dict:
    ids = list(range(1, 90))
    selection = _write(
        root / "selection.json",
        {"schema": "lex_webarena_verified_pilot/1", "tasks": [{"task_id": i} for i in ids]},
    )
    dataset_rows = [
        {"task_id": i, "task": {"intent": f"task {i}"}, "evaluation_input": {"task_type": "ACTION"}}
        for i in ids
    ]
    dataset = _write(
        root / "dataset.json",
        {"schema": "lex_webarena_verified_trajectory_dataset/1", "trajectories": dataset_rows},
    )
    methods = [
        {"id": name, "engine": "chromium" if name == "chromium" else "moli"}
        for name in ("chromium", "moli-old", "moli-fixed")
    ]
    profile = _write(
        root / "profile.json",
        {
            "methods": methods,
            "dataset": {"path": str(dataset), "sha256": _sha(dataset)},
            "task_selection": {"path": str(selection), "sha256": _sha(selection)},
            "execution": {
                "method_parallelism": 1,
                "task_parallelism": 1,
                "reset_before_every_task": True,
                "reset_before_every_method": True,
                "teardown_between_methods": True,
                "retry_failed_task": False,
            },
        },
    )
    source = _write(root / "source.json", {"task_ids": ids, "status": "current-source-verified"})
    validity = _write(
        root / "validity.json",
        {
            "schema": "webarena_reference_validity/1",
            "dataset_sha256": _sha(dataset),
            "tasks": [
                {
                    "task_id": i,
                    "status": "valid",
                    "reason": "Current source observation",
                    "source_path": str(source),
                    "source_sha256": _sha(source),
                }
                for i in ids
            ],
        },
    )
    pairs = {}
    for method in methods:
        name = method["id"]
        binary_sha = (
            "a" * 64 if name == "chromium" else ("b" * 64 if name == "moli-old" else "c" * 64)
        )
        manifest_rows = []
        result_rows = []
        for i in ids:
            actions = [{"action": "click", "selector": f"#{i}"}]
            action_sha = _digest(actions)
            manifest_rows.append(
                {
                    "task_id": i,
                    "task_contract_sha256": _digest(
                        {
                            "task": dataset_rows[i - 1]["task"],
                            "evaluation_input": dataset_rows[i - 1]["evaluation_input"],
                        }
                    ),
                    "actions": actions,
                    "bound_calls_sha256": action_sha,
                    "source_actions": actions,
                    "source_calls_sha256": action_sha,
                }
            )
            directory = root / name / f"task-{i}"
            trace = _write(
                directory / "trace.json",
                [{"index": 1, "status": "success", "arguments": actions[0], "duration_ms": 1}],
            )
            readiness = _write(directory / "readiness.json", [])
            site_reset = _write(directory / "site-reset.json", {"method_id": name, "task_id": i})
            actual = _write(directory / "actual_response.json", {"status": "SUCCESS"})
            evaluation = _write(directory / "evaluation.json", {"score": 1})
            network = directory / "network.har"
            network.write_text("{}", encoding="utf-8")
            result_rows.append(
                {
                    "task_id": i,
                    "status": "pass",
                    "score": 1,
                    "action_count": 1,
                    "executed_actions_sha256": action_sha,
                    "completed_actions": 1,
                    "duration_ms": 2,
                    "error": "",
                    "evidence_sha256": {
                        "trace": _sha(trace),
                        "readiness": _sha(readiness),
                        "site_reset": _sha(site_reset),
                        "network_har": _sha(network),
                        "actual_response": _sha(actual),
                        "evaluation": _sha(evaluation),
                    },
                }
            )
        manifest = _write(
            root / f"{name}-manifest.json",
            {
                "schema": "webarena_three_method_inputs/1",
                "promotable": True,
                "method_id": name,
                "engine": method["engine"],
                "dataset_sha256": _sha(dataset),
                "browser_binary_sha256": binary_sha,
                "site_map_sha256": "d" * 64,
                "selection_sha256": _sha(selection),
                "task_ids": ids,
                "tasks": manifest_rows,
                "run_count": 1,
                "retry_count": 0,
                "site_reset_between_tasks": True,
            },
        )
        reset_sha = {"chromium": "e" * 64, "moli-old": "f" * 64, "moli-fixed": "0" * 64}[name]
        reference_hashes = {
            "manifest": _sha(manifest),
            "frozen_trajectories": _sha(dataset),
            "browser_binary": binary_sha,
            "site_map": "d" * 64,
            "task_selection": _sha(selection),
            "task_reset_hook": reset_sha,
        }
        receipt = _write(
            root / name / "receipt.json",
            {
                "schema": "webarena_three_method_cohort/1",
                "method_id": name,
                "engine": method["engine"],
                "promotable": True,
                "certified": True,
                "certification_independent_of_pass_rate": True,
                "run_count": 1,
                "retry_count": 0,
                "site_reset_between_tasks": True,
                "browser_context_per_task": "fresh",
                "manifest_sha256": _sha(manifest),
                "dataset_sha256": _sha(dataset),
                "browser_binary_sha256": binary_sha,
                "site_map_sha256": "d" * 64,
                "task_reset_hook_sha256": reset_sha,
                "reference_sha256_before": reference_hashes,
                "reference_sha256_after": reference_hashes,
                "declared_task_ids": ids,
                "executed_task_ids": ids,
                "results": result_rows,
            },
        )
        pairs[name] = (manifest, receipt)
    return {
        "profile_path": profile,
        "selection_path": selection,
        "dataset_path": dataset,
        "validity_path": validity,
        "cohort_inputs": pairs,
    }


def test_aggregate_has_exact_three_method_selected_task_matrix(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    result = aggregate(**inputs)
    assert len(result["attempts"]) == len(result["physical_executions"]) == 267
    assert {row["participant_id"] for row in result["attempts"]} == {
        "chromium",
        "moli-old",
        "moli-fixed",
    }
    assert all(row["reference_validity"]["status"] == "valid" for row in result["attempts"])
    assert all(
        row["task_contract_sha256"] and row["planned_actions_sha256"] for row in result["attempts"]
    )


def test_rejects_incomplete_old_cohort(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    receipt = inputs["cohort_inputs"]["moli-fixed"][1]
    data = json.loads(receipt.read_text())
    data["results"] = data["results"][:36]
    _write(receipt, data)
    with pytest.raises(ValueError, match="exactly one execution"):
        aggregate(**inputs)


def test_rejects_cross_method_task_contract(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    manifest = inputs["cohort_inputs"]["moli-fixed"][0]
    data = json.loads(manifest.read_text())
    data["tasks"][0]["task_contract_sha256"] = "0" * 64
    _write(manifest, data)
    receipt = inputs["cohort_inputs"]["moli-fixed"][1]
    receipt_data = json.loads(receipt.read_text())
    receipt_data["manifest_sha256"] = _sha(manifest)
    receipt_data["reference_sha256_before"]["manifest"] = _sha(manifest)
    receipt_data["reference_sha256_after"]["manifest"] = _sha(manifest)
    _write(receipt, receipt_data)
    with pytest.raises(ValueError, match="execution differs"):
        aggregate(**inputs)


def test_rejects_missing_evidence_or_validity(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    (tmp_path / "moli-old" / "task-2" / "evaluation.json").unlink()
    with pytest.raises(ValueError, match="missing or symlinked evidence"):
        aggregate(**inputs)
    inputs = _fixture(tmp_path)
    validity = inputs["validity_path"]
    data = json.loads(validity.read_text())
    data["tasks"][0].pop("status")
    _write(validity, data)
    with pytest.raises(ValueError, match="validity lacks current source proof"):
        aggregate(**inputs)
