from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from harness.trajectory_replay import catalog_comparison
from harness.trajectory_replay.catalog_contracts import file_sha256
from harness.trajectory_replay.webarena_replay import _json_sha256


def _write(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return file_sha256(path)


def test_catalog_dataset_uses_the_executed_catalog_actions(monkeypatch, tmp_path: Path) -> None:
    actions = [{"action": "navigate", "url": "https://target.example/task"}]
    catalog = {
        "rows": [
            {
                "task_id": 0,
                "mode": "direct",
                "source_kind": "reviewed",
                "source_actions": actions,
                "source_site_urls": {},
                "bound_calls_sha256": _json_sha256(actions),
            }
        ]
    }
    monkeypatch.setattr(
        catalog_comparison,
        "load_catalog_inputs",
        lambda *_args, **_kwargs: (catalog, {}, {}),
    )
    official = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "intent": "Open the task",
            "intent_template_id": 1,
            "revision": 1,
            "sites": ["wikipedia"],
            "start_urls": ["https://target.example/task"],
            "eval": [{"expected": {"task_type": "NAVIGATE", "status": "SUCCESS"}}],
        }
    )
    monkeypatch.setattr(
        catalog_comparison,
        "remote_benchmark",
        lambda _sites: SimpleNamespace(get_task=lambda _task_id: official),
    )
    monkeypatch.setattr(
        catalog_comparison,
        "load_corrections",
        lambda: SimpleNamespace(records={}),
    )
    monkeypatch.setattr(
        catalog_comparison,
        "rebind_remote_calls",
        lambda calls, *_args: calls,
    )
    monkeypatch.setattr(catalog_comparison, "row_source_sites", lambda *_args: {})

    catalog_path = tmp_path / "catalog.json"
    _write(catalog_path, catalog)

    dataset = catalog_comparison.build_catalog_comparison_dataset(
        catalog_path=catalog_path,
        suite_path=tmp_path / "suite.json",
        site_map_path=tmp_path / "sites.json",
    )

    assert dataset["counts"]["verified_tasks"] == 1
    assert dataset["trajectories"][0]["tool_calls"] == actions
    assert dataset["trajectories"][0]["evaluation_input"]["status"] == "SUCCESS"


def _participant_fixture(
    root: Path,
    *,
    participant_id: str,
    engine: str,
    rows: list[dict],
    site_reset: Path,
) -> dict:
    local_site_reset = root / "site-reset.json"
    _write(local_site_reset, json.loads(site_reset.read_text(encoding="utf-8")))
    ledger_rows = []
    for task_id, specification in enumerate(rows):
        attempts = []
        if specification.get("setup_failure"):
            attempts.append(
                {
                    "status": "candidate_setup_failure",
                    "score": None,
                    "progress_sha256": "0" * 64,
                }
            )
        run = root / f"{participant_id}-{task_id}"
        action = {"action": "navigate", "url": f"https://example.test/{task_id}"}
        error = specification.get("error", "")
        status = specification["status"]
        evidence = {
            "trace": _write(run / f"task-{task_id}" / "trace.json", []),
            "readiness": _write(run / f"task-{task_id}" / "readiness.json", []),
        }
        if specification.get("evaluation", status == "pass"):
            evidence["evaluation"] = _write(
                run / f"task-{task_id}" / "evaluation.json",
                {"status": status, "score": 1.0 if status == "pass" else 0.0},
            )
        physical = {
            "task_id": task_id,
            "status": status,
            "score": 1.0 if status == "pass" else None,
            "action_count": 1,
            "completed_actions": 0 if error else 1,
            "duration_ms": 10 + task_id,
            "executed_actions_sha256": _json_sha256([action]),
            "error": error,
            "evidence_sha256": evidence,
        }
        progress = run / "progress.jsonl"
        progress.parent.mkdir(parents=True, exist_ok=True)
        progress.write_text(json.dumps(physical) + "\n", encoding="utf-8")
        _write(
            run / "inputs.json",
            {
                "engine": engine,
                "binary_sha256": participant_id[0] * 64,
                "site_reset_receipt_sha256": file_sha256(local_site_reset),
            },
        )
        attempts.append(
            {
                "status": status,
                "score": physical["score"],
                "progress_sha256": file_sha256(progress),
            }
        )
        ledger_rows.append({"task_id": task_id, "status": status, "attempts": attempts})
    ledger = root / f"{participant_id}-ledger.json"
    _write(
        ledger,
        {
            "participant": {"engine": engine, "backend": "playwright"},
            "site_reset_receipt_sha256": file_sha256(local_site_reset),
            "rows": ledger_rows,
        },
    )
    return {
        "id": participant_id,
        "engine": engine,
        "ledger_path": str(ledger),
        "evidence_root": str(root),
    }


def test_catalog_results_keep_one_logical_result_and_valid_physical_history(
    tmp_path: Path,
) -> None:
    dataset = {
        "trajectories": [
            {
                "task_id": task_id,
                "task": {"intent": f"Task {task_id}", "sites": ["wikipedia"]},
                "evaluation_input": {"task_type": "NAVIGATE", "status": "SUCCESS"},
                "tool_calls": [
                    {"action": "navigate", "url": f"https://example.test/{task_id}"}
                ],
            }
            for task_id in range(2)
        ]
    }
    site_reset = tmp_path / "site-reset.json"
    _write(site_reset, {"status": "ready"})
    participants = [
        _participant_fixture(
            tmp_path / "a",
            participant_id="moli",
            engine="moli",
            rows=[{"status": "pass"}, {"status": "pass"}],
            site_reset=site_reset,
        ),
        _participant_fixture(
            tmp_path / "b",
            participant_id="lightpanda",
            engine="lightpanda",
            rows=[
                {"status": "pass", "setup_failure": True},
                {
                    "status": "fail",
                    "error": "Traceback in _login_context: element is outside of the viewport",
                    "evaluation": False,
                },
            ],
            site_reset=site_reset,
        ),
    ]

    results = catalog_comparison.build_catalog_comparison_results(
        dataset=dataset,
        dataset_sha256="1" * 64,
        profile_sha256="2" * 64,
        scope_sha256="3" * 64,
        site_map_sha256="4" * 64,
        participants=participants,
    )

    assert len(results["attempts"]) == 4
    assert len(results["physical_executions"]) == 4
    lightpanda = {
        row["task_id"]: row
        for row in results["attempts"]
        if row["participant_id"] == "lightpanda"
    }
    assert lightpanda[0]["status"] == "pass"
    assert lightpanda[1]["process_status"] == "fail"
    assert lightpanda[1]["failure_stage"] == "browser_session_setup"
    assert lightpanda[1]["evaluator_status"] == "not_run"
    assert lightpanda[1]["evidence"]["site_reset"]["sha256"] == file_sha256(
        site_reset
    )
