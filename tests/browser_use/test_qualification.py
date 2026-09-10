from __future__ import annotations

import hashlib
import json

import pytest

from harness.browser_use.qualification import (
    apply_adjudication,
    build_evidence_revalidation_suite,
    build_repair_queue,
    build_repair_validation_suite,
    task_contract_sha256,
)
from harness.browser_use.suite import BrowserTask, TaskSuite, load_suite


def _task(task_id, *, response_builder=None, empty=False):
    actions = [] if empty else [{"action": "click", "selector": "#x"}]
    ground_truth = {"expected_actions": [action["action"] for action in actions]}
    if response_builder:
        ground_truth["response_builder"] = response_builder
    return BrowserTask(
        task_id,
        task_id,
        tuple(actions),
        (),
        (),
        "simple",
        ground_truth,
        {"url": "https://example.com"},
        {"kind": "webarena_verified", "task_id": 1, "revision": 2},
        {"kind": "webarena_verified"},
        {
            "schema": "lex_browser_trajectory/1",
            "provenance": {"kind": "test"},
            "actions": actions,
        },
    )


def test_repair_queue_covers_every_task_and_separates_trace_from_response_work(tmp_path):
    suite = TaskSuite(
        "lex_browser_use_tasks/4",
        (
            _task("pass"),
            _task("response"),
            _task("repair", response_builder={"kind": "observed_shape"}),
            _task("empty", empty=True),
        ),
    )
    attempts = tmp_path / "attempts.jsonl"
    rows = [
        {
            "task_id": "pass",
            "engine": "chromium",
            "is_retry": False,
            "combined_status": "pass",
            "status": "pass",
            "evidence_status": "pass",
            "process_status": "pass",
            "result_status": "pass",
            "trace": [],
            "qualification_run": True,
        },
        {
            "task_id": "response",
            "engine": "chromium",
            "is_retry": False,
            "combined_status": "fail",
            "process_status": "pass",
            "result_status": "fail",
            "trace": [],
            "qualification_run": True,
        },
        {
            "task_id": "repair",
            "engine": "chromium",
            "is_retry": False,
            "combined_status": "fail",
            "process_status": "fail",
            "result_status": "fail",
            "trace": [{"index": 1, "status": "fail"}],
            "qualification_run": True,
        },
        {
            "task_id": "empty",
            "engine": "chromium",
            "is_retry": False,
            "combined_status": "fail",
            "process_status": "pass",
            "result_status": "fail",
            "trace": [],
            "qualification_run": True,
        },
    ]
    for task, row in zip(suite.tasks, rows, strict=True):
        evidence_directory = tmp_path / task.task_id
        evidence_directory.mkdir()
        row["evidence_directory"] = str(evidence_directory)
        row["task_contract_sha256"] = task_contract_sha256(
            {
                "id": task.task_id,
                "title": task.title,
                "trajectory": task.trajectory,
                "evaluator": task.evaluator,
                "environment": task.environment,
                "ground_truth": task.ground_truth,
            }
        )
    attempts.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    result = build_repair_queue(suite, attempts)

    assert [item["disposition"] for item in result["tasks"]] == [
        "eligible",
        "needs_response_annotation",
        "needs_trace_repair",
        "needs_trace_repair",
    ]
    assert result["tasks"][2]["failed_step"]["index"] == 1
    assert result["tasks"][0]["adjudication"] == {
        "status": "eligible",
        "validated_attempt_path": str(tmp_path / "pass" / "attempt.json"),
    }


def test_passing_outcome_without_required_final_evidence_needs_revalidation(tmp_path):
    task = _task("legacy-pass")
    attempt_dir = tmp_path / "legacy-pass"
    attempt_dir.mkdir()
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        json.dumps(
            {
                "task_id": task.task_id,
                "engine": "chromium",
                "is_retry": False,
                "qualification_run": True,
                "status": "pass",
                "combined_status": "pass",
                "process_status": "pass",
                "result_status": "pass",
                "evidence_status": "fail",
                "trace": [],
                "evidence_directory": str(attempt_dir),
                "task_contract_sha256": task_contract_sha256(
                    {
                        "id": task.task_id,
                        "title": task.title,
                        "trajectory": task.trajectory,
                        "evaluator": task.evaluator,
                        "environment": task.environment,
                        "ground_truth": task.ground_truth,
                    }
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_repair_queue(TaskSuite("lex_browser_use_tasks/4", (task,)), attempts)

    assert result["tasks"][0]["disposition"] == "needs_evidence_revalidation"
    assert result["tasks"][0]["adjudication"] is None


def test_repaired_task_cannot_be_eligible_without_validated_attempt():
    suite = {
        "benchmark": {},
        "tasks": [{"id": "x", "ground_truth": {"qualification": {"status": "pending"}}}],
    }
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {
                "task_id": "x",
                "disposition": "needs_trace_repair",
                "adjudication": {"status": "eligible", "reason": "repair validated"},
            }
        ],
    }

    with pytest.raises(ValueError, match="validated_attempt_path"):
        apply_adjudication(suite, qualification)


def test_unchanged_passing_task_still_requires_bound_qualification_attempt():
    suite = {
        "benchmark": {},
        "tasks": [{"id": "x", "ground_truth": {"qualification": {"status": "pending"}}}],
    }
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {
                "task_id": "x",
                "disposition": "eligible",
                "adjudication": {"status": "eligible"},
            }
        ],
    }

    with pytest.raises(ValueError, match="validated_attempt_path"):
        apply_adjudication(suite, qualification)


def test_empty_candidate_cannot_be_adjudicated_eligible_without_correction():
    suite = {
        "benchmark": {},
        "tasks": [
            {
                "id": "x",
                "trajectory": {"schema": "lex_browser_trajectory/1", "actions": []},
                "ground_truth": {"qualification": {"status": "pending"}},
            }
        ],
    }
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {
                "task_id": "x",
                "disposition": "needs_trace_repair",
                "adjudication": {"status": "eligible", "reason": "claimed valid"},
            }
        ],
    }

    with pytest.raises(ValueError, match="non-empty trajectory"):
        apply_adjudication(suite, qualification)


def test_correction_is_applied_only_with_passing_primary_qualification_attempt(tmp_path):
    attempt = tmp_path / "attempt.json"
    attempt.write_text(
        json.dumps(
            {
                "task_id": "x",
                "engine": "chromium",
                "is_retry": False,
                "qualification_run": True,
                "status": "pass",
                "combined_status": "pass",
                "evidence_status": "pass",
                "evidence_directory": str(tmp_path),
                "final_page_evidence": {
                    "status": "complete",
                    "screenshot": {"path": "final.png", "sha256": "0" * 64},
                    "page": {"path": "final-page.json", "sha256": "0" * 64},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "network.har").write_text(
        json.dumps(
            {
                "log": {
                    "creator": {"name": "browser-eval-cdp", "version": "2"},
                    "entries": [],
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "final.png").write_bytes(b"image")
    (tmp_path / "final-page.json").write_text("{}", encoding="utf-8")
    suite = {
        "benchmark": {},
        "tasks": [
            {
                "id": "x",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "provenance": {"kind": "official_human"},
                    "actions": [{"action": "click", "selector": "#old"}],
                },
                "ground_truth": {
                    "expected_actions": ["click"],
                    "qualification": {"status": "pending"},
                },
            }
        ],
    }
    corrected = {
        "schema": "lex_browser_trajectory/1",
        "provenance": {"correction_reason": "upstream selector no longer exists"},
        "actions": [
            {"action": "click", "target": {"by": "role", "role": "button", "name": "Save"}}
        ],
    }
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {
                "task_id": "x",
                "disposition": "needs_trace_repair",
                "adjudication": {
                    "status": "eligible",
                    "reason": "replaced a stale selector",
                    "validated_attempt_path": str(attempt),
                    "corrected_trajectory": corrected,
                },
            }
        ],
    }
    validation_suite = build_repair_validation_suite(suite, qualification)
    validation_task = validation_suite["tasks"][0]
    assert validation_task["ground_truth"]["qualification"] == {"status": "pending"}
    assert validation_task["trajectory"]["provenance"]["kind"] == (
        "codex_corrected_webarena_trajectory"
    )
    attempt_payload = json.loads(attempt.read_text(encoding="utf-8"))
    attempt_payload["task_contract_sha256"] = task_contract_sha256(validation_task)
    attempt_payload["final_page_evidence"]["screenshot"]["sha256"] = hashlib.sha256(
        (tmp_path / "final.png").read_bytes()
    ).hexdigest()
    attempt_payload["final_page_evidence"]["page"]["sha256"] = hashlib.sha256(
        (tmp_path / "final-page.json").read_bytes()
    ).hexdigest()
    attempt.write_text(json.dumps(attempt_payload), encoding="utf-8")

    result = apply_adjudication(suite, qualification)

    task = result["tasks"][0]
    assert task["trajectory"]["actions"] == corrected["actions"]
    assert task["ground_truth"]["qualification"]["validated_run_sha256"]
    assert "validated_attempt_path" not in task["ground_truth"]["qualification"]
    assert task["trajectory"]["provenance"]["original_trajectory_sha256"]


def test_repaired_task_cannot_use_old_har_evidence(tmp_path):
    attempt = tmp_path / "attempt.json"
    suite = {
        "benchmark": {},
        "tasks": [
            {
                "id": "x",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "actions": [{"action": "click", "selector": "#x"}],
                },
                "ground_truth": {"qualification": {"status": "pending"}},
            }
        ],
    }
    attempt.write_text(
        json.dumps(
            {
                "task_id": "x",
                "engine": "chromium",
                "is_retry": False,
                "qualification_run": True,
                "status": "pass",
                "combined_status": "pass",
                "evidence_status": "pass",
                "evidence_directory": str(tmp_path),
                "task_contract_sha256": task_contract_sha256(suite["tasks"][0]),
                "final_page_evidence": {
                    "status": "complete",
                    "screenshot": {"path": "final.png", "sha256": "0" * 64},
                    "page": {"path": "final-page.json", "sha256": "0" * 64},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "network.har").write_text(
        json.dumps({"log": {"creator": {"name": "browser-eval-cdp", "version": "1"}}}),
        encoding="utf-8",
    )
    (tmp_path / "final.png").write_bytes(b"image")
    (tmp_path / "final-page.json").write_text("{}", encoding="utf-8")
    payload = json.loads(attempt.read_text(encoding="utf-8"))
    payload["final_page_evidence"]["screenshot"]["sha256"] = hashlib.sha256(
        (tmp_path / "final.png").read_bytes()
    ).hexdigest()
    payload["final_page_evidence"]["page"]["sha256"] = hashlib.sha256(
        (tmp_path / "final-page.json").read_bytes()
    ).hexdigest()
    attempt.write_text(json.dumps(payload), encoding="utf-8")
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {
                "task_id": "x",
                "disposition": "eligible",
                "adjudication": {
                    "status": "eligible",
                    "validated_attempt_path": str(attempt),
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="HAR creator"):
        apply_adjudication(suite, qualification)


def test_repair_validation_suite_requires_the_declared_material_change():
    suite = {
        "benchmark": {},
        "tasks": [
            {
                "id": "x",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "actions": [{"action": "click", "selector": "#old"}],
                },
                "ground_truth": {
                    "expected_actions": ["click"],
                    "qualification": {"status": "pending"},
                },
            }
        ],
    }
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {
                "task_id": "x",
                "disposition": "needs_trace_repair",
                "adjudication": {"status": "eligible", "reason": "claimed repair"},
            }
        ],
    }

    with pytest.raises(ValueError, match="requires corrected_trajectory"):
        build_repair_validation_suite(suite, qualification)


def test_repair_validation_suite_is_loadable_and_keeps_contract_hash(tmp_path):
    suite = {
        "schema": "lex_browser_use_tasks/4",
        "benchmark": {},
        "tasks": [
            {
                "id": "x",
                "title": "repair candidate",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "provenance": {"kind": "official_human"},
                    "actions": [{"action": "click", "selector": "#old"}],
                },
                "assertions": [],
                "tags": [],
                "difficulty": "simple",
                "ground_truth": {
                    "expected_actions": ["click"],
                    "qualification": {"status": "pending"},
                },
                "source": {"url": "https://example.test/trace"},
                "evaluator": {"kind": "webarena_verified", "task_id": 1, "revision": 2},
                "environment": {
                    "kind": "webarena_verified",
                    "sites": ["shopping"],
                    "start_urls": ["__SHOPPING__"],
                },
            }
        ],
    }
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {
                "task_id": "x",
                "disposition": "needs_trace_repair",
                "adjudication": {
                    "status": "eligible",
                    "reason": "replace stale selector",
                    "corrected_trajectory": {
                        "schema": "lex_browser_trajectory/1",
                        "actions": [
                            {
                                "action": "click",
                                "target": {"by": "role", "role": "button", "name": "Save"},
                            }
                        ],
                    },
                },
            }
        ],
    }
    result = build_repair_validation_suite(suite, qualification)
    path = tmp_path / "repair-suite.json"
    path.write_text(json.dumps(result), encoding="utf-8")

    loaded = load_suite(path)

    raw_contract = task_contract_sha256(result["tasks"][0])
    loaded_task = loaded.tasks[0]
    loaded_contract = task_contract_sha256(
        {
            "id": loaded_task.task_id,
            "title": loaded_task.title,
            "trajectory": loaded_task.trajectory,
            "evaluator": loaded_task.evaluator,
            "environment": loaded_task.environment,
            "ground_truth": loaded_task.ground_truth,
        }
    )
    assert raw_contract == loaded_contract


def test_evidence_revalidation_includes_direct_and_corrected_eligible_tasks():
    suite = {
        "benchmark": {"name": "WebArena"},
        "tasks": [
            {
                "id": "direct",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "actions": [{"action": "click"}],
                },
                "ground_truth": {"expected_actions": ["click"]},
            },
            {
                "id": "corrected",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "actions": [{"action": "click"}],
                },
                "ground_truth": {"expected_actions": ["click"]},
            },
            {
                "id": "pending",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "actions": [{"action": "click"}],
                },
                "ground_truth": {"expected_actions": ["click"]},
            },
        ],
    }
    qualification = {
        "tasks": [
            {"task_id": "direct", "adjudication": {"status": "eligible"}},
            {
                "task_id": "corrected",
                "adjudication": {
                    "status": "eligible",
                    "corrected_trajectory": {
                        "schema": "lex_browser_trajectory/1",
                        "actions": [{"action": "snapshot", "selector": "body", "save_as": "page"}],
                    },
                    "response_builder": {
                        "kind": "observed_shape",
                        "task_type": "retrieve",
                        "shape": [{"$output": "page"}],
                    },
                },
            },
            {"task_id": "pending", "adjudication": None},
        ]
    }

    result = build_evidence_revalidation_suite(suite, qualification)

    assert [task["id"] for task in result["tasks"]] == ["direct", "corrected"]
    assert result["tasks"][0]["ground_truth"]["qualification"] == {"status": "pending"}
    assert result["tasks"][1]["trajectory"]["actions"][0]["action"] == "snapshot"
    assert result["benchmark"]["evidence_revalidation"]["task_count"] == 2


def test_skipped_task_requires_public_reason():
    suite = {
        "benchmark": {},
        "tasks": [{"id": "x", "ground_truth": {"qualification": {"status": "pending"}}}],
    }
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {
                "task_id": "x",
                "disposition": "needs_trace_repair",
                "adjudication": {"status": "skipped"},
            }
        ],
    }

    with pytest.raises(ValueError, match="requires reason"):
        apply_adjudication(suite, qualification)
