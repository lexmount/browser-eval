from __future__ import annotations

import json

from harness.browser_use.qualification import task_contract_sha256
from scripts.finalize_webarena_adjudication import finalize_adjudication


def _task(task_id: str, text: str = "old"):
    return {
        "id": task_id,
        "title": "Task",
        "trajectory": {
            "schema": "lex_browser_trajectory/1",
            "actions": [{"action": "fill", "selector": "#value", "text": text}],
        },
        "evaluator": {"kind": "webarena_verified"},
        "environment": {"kind": "webarena_verified"},
        "ground_truth": {
            "response_builder": {"kind": "null_after_verified_path"},
            "qualification": {"status": "pending"},
        },
    }


def test_overlays_only_triple_pass_and_skips_every_unresolved_task(tmp_path):
    original = _task("webarena-1")
    unresolved = _task("webarena-2")
    repaired = _task("webarena-1", "fixed")
    repaired["ground_truth"]["qualification"] = {
        "status": "pending",
        "reason": "Exact corrected field.",
    }
    evidence = tmp_path / "task" / "attempt-1"
    evidence.mkdir(parents=True)
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        json.dumps(
            {
                "task_id": "webarena-1",
                "engine": "chromium",
                "qualification_run": True,
                "is_retry": False,
                "attempt": 1,
                "process_status": "pass",
                "result_status": "pass",
                "evidence_status": "pass",
                "evidence_directory": str(evidence),
                "task_contract_sha256": task_contract_sha256(repaired),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {"task_id": "webarena-1", "disposition": "needs_trace_repair"},
            {"task_id": "webarena-2", "disposition": "needs_trace_repair"},
        ],
    }

    result = finalize_adjudication(
        {"tasks": [original, unresolved]},
        qualification,
        [({"tasks": [repaired]}, attempts)],
        {},
    )

    assert result["tasks"][0]["adjudication"]["status"] == "eligible"
    assert result["tasks"][0]["adjudication"]["corrected_trajectory"] == repaired[
        "trajectory"
    ]
    assert result["tasks"][1]["adjudication"]["status"] == "skipped"


def test_explicit_invalidation_replaces_stale_eligibility():
    task = _task("webarena-1")
    qualification = {
        "tasks": [
            {
                "task_id": "webarena-1",
                "disposition": "eligible",
                "adjudication": {
                    "status": "eligible",
                    "validated_attempt_path": "/stale/attempt.json",
                },
            }
        ]
    }

    result = finalize_adjudication(
        {"tasks": [task]},
        qualification,
        [],
        {"webarena-1": "Later cold revalidation failed."},
    )

    assert result["tasks"][0]["adjudication"] == {
        "status": "skipped",
        "reason": "Later cold revalidation failed.",
    }


def test_evidence_only_revalidation_does_not_invent_a_repair_reason(tmp_path):
    task = _task("webarena-1")
    evidence = tmp_path / "task" / "attempt-1"
    evidence.mkdir(parents=True)
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        json.dumps(
            {
                "task_id": "webarena-1",
                "engine": "chromium",
                "qualification_run": True,
                "is_retry": False,
                "attempt": 1,
                "process_status": "pass",
                "result_status": "pass",
                "evidence_status": "pass",
                "evidence_directory": str(evidence),
                "task_contract_sha256": task_contract_sha256(task),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = finalize_adjudication(
        {"tasks": [task]},
        {
            "tasks": [
                {
                    "task_id": "webarena-1",
                    "disposition": "needs_evidence_revalidation",
                }
            ]
        },
        [({"tasks": [task]}, attempts)],
        {},
    )

    assert result["tasks"][0]["adjudication"]["status"] == "eligible"
    assert "reason" not in result["tasks"][0]["adjudication"]
