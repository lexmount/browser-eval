from __future__ import annotations

import copy

import pytest

from harness.browser_use.result_audit import ResultAuditError, audit_result_set


def _fixture():
    manifest = {"suite": [{"task_id": "task-1"}]}
    attempt = {
        "attempt": 1,
        "is_retry": False,
        "status": "pass",
        "result_status": "pass",
        "process_status": "pass",
        "combined_status": "pass",
        "error": "",
        "process_validation": {
            "expected_actions": ["click"],
            "actual_actions": ["click"],
            "all_calls_passed": True,
            "passed": True,
        },
        "validation": [{"status": "success", "score": 1.0}],
        "trace": [
            {"action": "click", "status": "pass"},
            {
                "action": "evaluate_result",
                "status": "pass",
                "checks": [{"status": "success", "score": 1.0}],
            },
        ],
        "network_har": {"status": "complete"},
        "final_page_evidence": {"status": "complete"},
        "evidence_status": "pass",
    }
    summary = {
        "by_task": {
            "task-1": {
                "attempts": {
                    "chromium": [copy.deepcopy(attempt)],
                    "moli": [copy.deepcopy(attempt)],
                }
            }
        },
        "functional": {
            "chromium": {"physical_attempts": 1, "result_final_passes": 1},
            "moli": {"physical_attempts": 1, "result_final_passes": 1},
        },
    }
    return manifest, summary


def test_audit_covers_every_logical_result_and_attempt():
    manifest, summary = _fixture()

    audit = audit_result_set(manifest, summary)

    assert audit["logical_results_reviewed"] == 2
    assert audit["physical_attempts_reviewed"] == 2
    assert audit["final_passes"] == {"chromium": 1, "moli": 1}


def test_audit_rejects_result_that_contradicts_official_evaluator():
    manifest, summary = _fixture()
    summary["by_task"]["task-1"]["attempts"]["moli"][0]["validation"][0]["score"] = 0

    with pytest.raises(ResultAuditError, match="result status contradicts evaluator"):
        audit_result_set(manifest, summary)


def test_audit_rejects_retry_after_passing_primary_result():
    manifest, summary = _fixture()
    retry = copy.deepcopy(summary["by_task"]["task-1"]["attempts"]["moli"][0])
    retry.update({"attempt": 2, "is_retry": True})
    summary["by_task"]["task-1"]["attempts"]["moli"].append(retry)
    summary["functional"]["moli"]["physical_attempts"] = 2

    with pytest.raises(ResultAuditError, match="retried after a passing primary"):
        audit_result_set(manifest, summary)
