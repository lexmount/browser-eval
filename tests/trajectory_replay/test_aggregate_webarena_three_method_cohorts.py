"""Fail-closed controls for the certified three-method replay aggregate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.aggregate_webarena_three_method_cohorts import _digest, _sha, aggregate, main


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _reset_receipt(method_id: str) -> dict:
    sites = {}
    for site in ("shopping_admin", "shopping", "gitlab", "reddit"):
        sites[site] = {
            "service_id": f"{site}-service",
            "image_digest": "a" * 64,
            "event_id": f"{site}-{method_id}",
            "event_status": "success",
            "old_pod": f"{site}-old-{method_id}",
            "new_pod": f"{site}-new-{method_id}",
            "data_volume_count": 0,
            "public_http_status": 200,
        }
        if site in {"gitlab", "reddit"}:
            sites[site]["baseline_check"] = {"row_count": 0}
    return {
        "schema": "webarena_remote_cohort_reset/1",
        "app_id": 195,
        "method_id": method_id,
        "task_id": None,
        "site_map_sha256": "d" * 64,
        "reset_scope": "once_before_method_cohort",
        "site_reset_between_tasks": False,
        "completed_at_unix": 1.0,
        "sites": sites,
    }


def _fixture(root: Path, *, reverse_inventory: bool = False, method_names=None) -> dict:
    ids = list(range(1, 91))
    inventory_ids = list(reversed(ids)) if reverse_inventory else ids
    selection = _write(
        root / "selection.json",
        {
            "schema": "lex_webarena_verified_pilot/1",
            "tasks": [{"task_id": i} for i in inventory_ids],
        },
    )
    dataset_rows = [
        {"task_id": i, "task": {"intent": f"task {i}"}, "evaluation_input": {"task_type": "ACTION"}}
        for i in inventory_ids
    ]
    dataset = _write(
        root / "dataset.json",
        {"schema": "lex_webarena_verified_trajectory_dataset/1", "trajectories": dataset_rows},
    )
    dataset_by_id = {row["task_id"]: row for row in dataset_rows}
    methods = [
        {"id": name, "engine": "chromium" if name == "chromium" else "moli"}
        for name in (method_names or ("chromium", "moli-old", "moli-fixed"))
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
                "reset_before_every_task": False,
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
                for i in inventory_ids
            ],
        },
    )
    pairs = {}
    for method in methods:
        name = method["id"]
        site_reset = _write(root / name / "site-reset.json", _reset_receipt(name))
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
                            "task": dataset_by_id[i]["task"],
                            "evaluation_input": dataset_by_id[i]["evaluation_input"],
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
                "site_reset_between_tasks": False,
            },
        )
        reset_sha = _digest({"reset_hook": name})
        reference_hashes = {
            "manifest": _sha(manifest),
            "frozen_trajectories": _sha(dataset),
            "browser_binary": binary_sha,
            "site_map": "d" * 64,
            "task_selection": _sha(selection),
            "cohort_reset_hook": reset_sha,
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
                "site_reset_between_tasks": False,
                "app_id": 195,
                "browser_context_per_task": "fresh",
                "manifest_sha256": _sha(manifest),
                "dataset_sha256": _sha(dataset),
                "browser_binary_sha256": binary_sha,
                "site_map_sha256": "d" * 64,
                "cohort_reset_hook_sha256": reset_sha,
                "cohort_reset_sha256": _sha(site_reset),
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
    assert len(result["attempts"]) == len(result["physical_executions"]) == 270
    from harness.trajectory_replay.participants import bind_participant, participant_registry
    registry = participant_registry([
        {"id": row["participant_id"], "engine": row["engine"]}
        for row in result["attempts"][::90]
    ])
    for row in result["physical_executions"]:
        assert bind_participant(row, registry)["engine"] == registry[row["participant_id"]]["engine"]
    assert {row["participant_id"] for row in result["attempts"]} == {
        "chromium",
        "moli-old",
        "moli-fixed",
    }
    assert all(row["reference_validity"]["status"] == "valid" for row in result["attempts"])
    assert all(
        row["task_contract_sha256"] and row["planned_actions_sha256"] for row in result["attempts"]
    )


def test_add_two_methods_preserves_existing_results(tmp_path: Path) -> None:
    original = ("chromium", "moli-old", "moli-fixed")
    old_inputs = _fixture(tmp_path / "old", method_names=original)
    before = aggregate(**old_inputs)
    inputs = _fixture(tmp_path / "expanded", method_names=(*original, "moli-tag", "moli-latest"))
    inputs["cohort_inputs"].update(old_inputs["cohort_inputs"])
    after = aggregate(**inputs)
    assert len(after["attempts"]) == len(after["physical_executions"]) == 450
    assert [row for row in after["attempts"] if row["participant_id"] in original] == before["attempts"]
    inputs["cohort_inputs"].pop("moli-latest")
    with pytest.raises(ValueError):
        aggregate(**inputs)


def test_rejects_incomplete_old_cohort(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    receipt = inputs["cohort_inputs"]["moli-fixed"][1]
    data = json.loads(receipt.read_text())
    data["results"] = data["results"][:36]
    _write(receipt, data)
    with pytest.raises(ValueError, match="exactly one execution"):
        aggregate(**inputs)


def test_cli_accepts_complete_five_method_matrix_without_overwriting(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path, method_names=("chromium", "moli-old", "moli-fixed", "moli-tag", "moli-latest"))
    output = tmp_path / "combined.json"
    args = ["--output", str(output)]
    for field, flag in (("profile_path", "--profile"), ("selection_path", "--selection"),
                        ("dataset_path", "--dataset"), ("validity_path", "--reference-validity")):
        args.extend([flag, str(inputs[field])])
    for method, (manifest, receipt) in inputs["cohort_inputs"].items():
        args.extend(["--manifest", f"{method}={manifest}", "--receipt", f"{method}={receipt}"])
    main(args)
    before = output.read_bytes()
    assert len(json.loads(before)["attempts"]) == 450
    with pytest.raises(ValueError, match="append-only"):
        main(args)
    assert output.read_bytes() == before


def test_sampling_inventory_order_does_not_override_numeric_execution(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path, reverse_inventory=True)
    result = aggregate(**inputs)
    assert [r["task_id"] for r in result["attempts"][:90]] == list(range(1, 91))


def test_rejects_execution_in_sampling_order(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path, reverse_inventory=True)
    receipt_path = inputs["cohort_inputs"]["chromium"][1]
    receipt = json.loads(receipt_path.read_text())
    receipt["executed_task_ids"].reverse()
    _write(receipt_path, receipt)
    with pytest.raises(ValueError, match="exactly one execution"):
        aggregate(**inputs)


@pytest.mark.parametrize("inventory", ["dataset_path", "validity_path"])
def test_rejects_duplicate_inventory_task_ids(tmp_path: Path, inventory: str) -> None:
    inputs = _fixture(tmp_path)
    path = inputs[inventory]
    data = json.loads(path.read_text())
    rows = data["trajectories" if inventory == "dataset_path" else "tasks"]
    rows[1]["task_id"] = rows[0]["task_id"]
    _write(path, data)
    if inventory == "dataset_path":
        profile = json.loads(inputs["profile_path"].read_text())
        profile["dataset"]["sha256"] = _sha(path)
        _write(inputs["profile_path"], profile)
    with pytest.raises(ValueError, match="selection|reference-validity"):
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


def test_rejects_hash_bound_but_false_site_reset(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    reset_path = tmp_path / "chromium/site-reset.json"
    reset = json.loads(reset_path.read_text())
    reset["sites"]["reddit"]["new_pod"] = reset["sites"]["reddit"]["old_pod"]
    _write(reset_path, reset)
    receipt_path = inputs["cohort_inputs"]["chromium"][1]
    receipt = json.loads(receipt_path.read_text())
    receipt["cohort_reset_sha256"] = _sha(reset_path)
    _write(receipt_path, receipt)
    with pytest.raises(ValueError, match="remote cohort reset is not proven"):
        aggregate(**inputs)


def test_rejects_reset_receipt_from_a_task(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    reset_path = tmp_path / "chromium/site-reset.json"
    reset = json.loads(reset_path.read_text())
    reset["task_id"] = 2
    _write(reset_path, reset)
    receipt_path = inputs["cohort_inputs"]["chromium"][1]
    receipt = json.loads(receipt_path.read_text())
    receipt["cohort_reset_sha256"] = _sha(reset_path)
    _write(receipt_path, receipt)
    with pytest.raises(
        ValueError, match="remote cohort reset|site reset belongs to another method"
    ):
        aggregate(**inputs)
