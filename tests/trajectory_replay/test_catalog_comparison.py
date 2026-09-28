from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.trajectory_replay import catalog_comparison
from harness.trajectory_replay.catalog_contracts import file_sha256
from harness.trajectory_replay.webarena_replay import _json_sha256


def _write(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return file_sha256(path)


def test_site_preparation_index_accepts_a_restart_receipt_name(tmp_path: Path) -> None:
    receipt = tmp_path / "four-site-restart-receipt-v2.json"
    digest = _write(receipt, {"schema": "lex_webarena_environment_preparation/1"})

    assert catalog_comparison._site_reset_index(tmp_path)[digest]["path"] == str(receipt)


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
    remote_browser: dict | None = None,
    request_bridge: dict | None = None,
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
            **(
                {"failure_stage": specification["failure_stage"]}
                if specification.get("failure_stage")
                else {}
            ),
            "evidence_sha256": evidence,
            **(
                {"runtime_metadata": remote_browser["runtime_metadata"]}
                if remote_browser is not None
                else {}
            ),
        }
        progress = run / "progress.jsonl"
        progress.parent.mkdir(parents=True, exist_ok=True)
        progress.write_text(json.dumps(physical) + "\n", encoding="utf-8")
        _write(
            run / "inputs.json",
            {
                "engine": engine,
                "binary_sha256": (
                    None if remote_browser is not None else participant_id[0] * 64
                ),
                **(
                    {"remote_browser": remote_browser}
                    if remote_browser is not None
                    else {}
                ),
                **(
                    {"request_bridge": request_bridge}
                    if request_bridge is not None
                    else {}
                ),
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
            "participant": {
                "engine": engine,
                "backend": "playwright",
                **(
                    {"remote_browser": remote_browser}
                    if remote_browser is not None
                    else {"binary_sha256": participant_id[0] * 64}
                ),
                **(
                    {"request_bridge": request_bridge}
                    if request_bridge is not None
                    else {}
                ),
            },
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


def _kitesurf_identity() -> dict:
    return {
        "provider": "cloudflare-browser-run",
        "browser": "kitesurf",
        "protocol": "cdp",
        "endpoint_contract": "wss://example/{account_id}?browser=kitesurf",
        "account_id_sha256": "a" * 64,
        "runtime_metadata": {
            "protocolVersion": "1.3",
            "product": "Chrome/145.0.0.0",
            "revision": "@kitesurf",
            "userAgent": "Kitesurf",
            "jsVersion": "14.5",
        },
    }


def _kitesurf_request_bridge() -> dict:
    return {
        "kind": "local_http_fulfill/1",
        "origins": ["http://private.example.test"],
    }


def test_catalog_results_reject_hosted_runtime_version_drift(tmp_path: Path) -> None:
    dataset = {
        "trajectories": [
            {
                "task_id": 0,
                "task": {"intent": "Task 0", "sites": ["wikipedia"]},
                "evaluation_input": {"task_type": "NAVIGATE", "status": "SUCCESS"},
                "tool_calls": [
                    {"action": "navigate", "url": "https://example.test/0"}
                ],
            }
        ]
    }
    reset = tmp_path / "site-reset.json"
    _write(reset, {"status": "ready"})
    participant = _participant_fixture(
        tmp_path / "remote",
        participant_id="kitesurf",
        engine="kitesurf",
        rows=[{"status": "pass"}],
        site_reset=reset,
        remote_browser=_kitesurf_identity(),
        request_bridge=_kitesurf_request_bridge(),
    )
    progress = tmp_path / "remote" / "kitesurf-0" / "progress.jsonl"
    physical = json.loads(progress.read_text(encoding="utf-8"))
    physical["runtime_metadata"]["product"] = "Chrome/146.0.0.0"
    progress.write_text(json.dumps(physical) + "\n", encoding="utf-8")
    ledger = Path(participant["ledger_path"])
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["rows"][0]["attempts"][0]["progress_sha256"] = file_sha256(progress)
    ledger.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="observed remote browser version changed"):
        catalog_comparison.build_catalog_comparison_results(
            dataset=dataset,
            dataset_sha256="1" * 64,
            profile_sha256="2" * 64,
            scope_sha256="3" * 64,
            site_map_sha256="4" * 64,
            participants=[participant],
        )


def test_catalog_results_reject_request_bridge_drift(tmp_path: Path) -> None:
    dataset = {
        "trajectories": [
            {
                "task_id": 0,
                "task": {"intent": "Task 0", "sites": ["wikipedia"]},
                "evaluation_input": {"task_type": "NAVIGATE", "status": "SUCCESS"},
                "tool_calls": [
                    {"action": "navigate", "url": "https://example.test/0"}
                ],
            }
        ]
    }
    reset = tmp_path / "site-reset.json"
    _write(reset, {"status": "ready"})
    bridge = _kitesurf_request_bridge()
    participant = _participant_fixture(
        tmp_path / "remote",
        participant_id="kitesurf",
        engine="kitesurf",
        rows=[{"status": "pass"}],
        site_reset=reset,
        remote_browser=_kitesurf_identity(),
        request_bridge=bridge,
    )
    inputs = tmp_path / "remote" / "kitesurf-0" / "inputs.json"
    payload = json.loads(inputs.read_text(encoding="utf-8"))
    payload["request_bridge"]["origins"] = ["http://other.example.test"]
    inputs.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="physical request bridge identity changed"):
        catalog_comparison.build_catalog_comparison_results(
            dataset=dataset,
            dataset_sha256="1" * 64,
            profile_sha256="2" * 64,
            scope_sha256="3" * 64,
            site_map_sha256="4" * 64,
            participants=[participant],
        )


def test_catalog_results_accept_each_binary_in_bound_repair_chain(tmp_path: Path) -> None:
    dataset = {
        "trajectories": [
            {
                "task_id": task_id,
                "task": {"intent": f"Task {task_id}", "sites": ["wikipedia"]},
                "evaluation_input": {"task_type": "NAVIGATE", "status": "SUCCESS"},
                "tool_calls": [
                    {
                        "action": "navigate",
                        "url": f"https://example.test/{task_id}",
                    }
                ],
            }
            for task_id in range(2)
        ]
    }
    reset = tmp_path / "site-reset.json"
    _write(reset, {"status": "ready"})
    participant = _participant_fixture(
        tmp_path / "moli",
        participant_id="moli",
        engine="moli",
        rows=[{"status": "pass"}, {"status": "pass"}],
        site_reset=reset,
    )
    first_binary = "m" * 64
    second_binary = "b" * 64
    second_inputs = tmp_path / "moli" / "moli-1" / "inputs.json"
    inputs = json.loads(second_inputs.read_text(encoding="utf-8"))
    inputs["binary_sha256"] = second_binary
    second_inputs.write_text(json.dumps(inputs) + "\n", encoding="utf-8")
    ledger = Path(participant["ledger_path"])
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["participant"].pop("binary_sha256")
    payload["participant"]["binary_sha256s"] = [first_binary, second_binary]
    ledger.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    results = catalog_comparison.build_catalog_comparison_results(
        dataset=dataset,
        dataset_sha256="1" * 64,
        profile_sha256="2" * 64,
        scope_sha256="3" * 64,
        site_map_sha256="4" * 64,
        participants=[participant],
    )

    assert {row["binary_sha256"] for row in results["attempts"]} == {
        first_binary,
        second_binary,
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


def test_catalog_results_preserve_runner_failure_stage(tmp_path: Path) -> None:
    dataset = {
        "trajectories": [
            {
                "task_id": 0,
                "task": {"intent": "Task 0", "sites": ["wikipedia"]},
                "evaluation_input": {"task_type": "NAVIGATE", "status": "SUCCESS"},
                "tool_calls": [{"action": "navigate", "url": "https://example.test/0"}],
            }
        ]
    }
    site_reset = tmp_path / "site-reset.json"
    _write(site_reset, {"status": "ready"})
    participant = _participant_fixture(
        tmp_path / "a",
        participant_id="obscura",
        engine="obscura",
        rows=[
            {
                "status": "fail",
                "error": "Evaluator crashed",
                "evaluation": False,
                "failure_stage": "official_evaluator_execution",
            }
        ],
        site_reset=site_reset,
    )
    results = catalog_comparison.build_catalog_comparison_results(
        dataset=dataset,
        dataset_sha256="1" * 64,
        profile_sha256="2" * 64,
        scope_sha256="3" * 64,
        site_map_sha256="4" * 64,
        participants=[participant],
    )
    assert results["attempts"][0]["failure_stage"] == "official_evaluator_execution"
