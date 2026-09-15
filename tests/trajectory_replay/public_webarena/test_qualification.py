from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from harness.trajectory_replay.public_webarena.har import HAR_CREATOR_NAME, HAR_CREATOR_VERSION
from harness.trajectory_replay.public_webarena.qualification import (
    QUALIFICATION_SCHEMA,
    apply_adjudication,
    build_evidence_revalidation_suite,
    build_repair_queue,
    build_repair_validation_suite,
    task_contract_sha256,
    trajectory_semantic_conflicts,
    validated_attempt_sha256s,
)
from harness.trajectory_replay.public_webarena.suite import BrowserTask, TaskSuite, load_suite


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


def _task_contract(task):
    if isinstance(task, BrowserTask):
        return {
            "id": task.task_id,
            "title": task.title,
            "trajectory": task.trajectory,
            "evaluator": task.evaluator,
            "environment": task.environment,
            "ground_truth": task.ground_truth,
            "source": task.source,
        }
    return task


def _write_validated_attempt(
    root,
    task,
    *,
    pass_index,
    run_id=None,
    statuses=None,
    har_version=HAR_CREATOR_VERSION,
):
    run_id = run_id or f"run-{pass_index}"
    evidence_directory = (
        root / run_id / str(task.task_id if isinstance(task, BrowserTask) else task["id"])
    )
    evidence_directory.mkdir(parents=True)
    screenshot = evidence_directory / "final.png"
    page = evidence_directory / "final-page.json"
    screenshot.write_bytes(f"image-{run_id}".encode())
    page.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    (evidence_directory / "network.har").write_text(
        json.dumps(
            {
                "log": {
                    "creator": {
                        "name": HAR_CREATOR_NAME,
                        "version": har_version,
                    },
                    "entries": [],
                }
            }
        ),
        encoding="utf-8",
    )
    row = {
        "task_id": task.task_id if isinstance(task, BrowserTask) else str(task["id"]),
        "engine": "chromium",
        "attempt": 1,
        "is_retry": False,
        "qualification_run": True,
        "qualification_pass_index": pass_index,
        "qualification_required_passes": 2,
        "run_id": run_id,
        "process_status": "pass",
        "result_status": "pass",
        "evidence_status": "pass",
        "status": "pass",
        "combined_status": "pass",
        "trace": [],
        "evidence_directory": str(evidence_directory),
        "task_contract_sha256": task_contract_sha256(_task_contract(task)),
        "final_page_evidence": {
            "status": "complete",
            "screenshot": {
                "path": "final.png",
                "sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest(),
            },
            "page": {
                "path": "final-page.json",
                "sha256": hashlib.sha256(page.read_bytes()).hexdigest(),
            },
        },
    }
    row.update(statuses or {})
    attempt = evidence_directory / "attempt.json"
    attempt.write_text(json.dumps(row), encoding="utf-8")
    return row, attempt


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
    rows = []
    status_by_task = {
        "pass": {},
        "response": {
            "combined_status": "fail",
            "status": "fail",
            "result_status": "fail",
        },
        "repair": {
            "combined_status": "fail",
            "status": "fail",
            "process_status": "fail",
            "result_status": "fail",
            "trace": [{"index": 1, "status": "fail"}],
        },
        "empty": {
            "combined_status": "fail",
            "status": "fail",
            "result_status": "fail",
        },
    }
    for pass_index in (1, 2):
        for task in suite.tasks:
            row, _ = _write_validated_attempt(
                tmp_path,
                task,
                pass_index=pass_index,
                run_id=f"run-{pass_index}-{task.task_id}",
                statuses=status_by_task[task.task_id],
            )
            rows.append(row)
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
    assert result["schema"] == QUALIFICATION_SCHEMA
    assert result["tasks"][0]["adjudication"] == {
        "status": "eligible",
        "validated_attempt_paths": [
            str(tmp_path / "run-1-pass" / "pass" / "attempt.json"),
            str(tmp_path / "run-2-pass" / "pass" / "attempt.json"),
        ],
    }


def test_passing_outcome_without_required_final_evidence_needs_revalidation(tmp_path):
    task = _task("legacy-pass")
    attempt_dir = tmp_path / "legacy-pass"
    attempt_dir.mkdir()
    attempts = tmp_path / "attempts.jsonl"
    rows = []
    for pass_index in (1, 2):
        rows.append(
            {
                "task_id": task.task_id,
                "engine": "chromium",
                "attempt": 1,
                "is_retry": False,
                "qualification_run": True,
                "qualification_pass_index": pass_index,
                "qualification_required_passes": 2,
                "run_id": f"run-{pass_index}",
                "status": "pass",
                "combined_status": "pass",
                "process_status": "pass",
                "result_status": "pass",
                "evidence_status": "fail",
                "trace": [],
                "evidence_directory": str(attempt_dir),
                "task_contract_sha256": task_contract_sha256(_task_contract(task)),
            }
        )
    attempts.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    result = build_repair_queue(TaskSuite("lex_browser_use_tasks/4", (task,)), attempts)

    assert result["tasks"][0]["disposition"] == "needs_evidence_revalidation"
    assert result["tasks"][0]["adjudication"] is None


def test_repair_queue_blocks_a_semantically_conflicting_passing_trace(tmp_path):
    original = _task("branch-conflict")
    action = {
        "action": "click",
        "target": {"by": "text", "value": "Commit to main branch"},
    }
    trajectory = {
        "schema": "lex_browser_trajectory/1",
        "provenance": {"kind": "test"},
        "actions": [action],
    }
    task = replace(
        original,
        title="Commit this change to a new branch",
        tool_calls=(action,),
        trajectory=trajectory,
        ground_truth={"expected_actions": ["click"]},
    )
    evidence_directory = tmp_path / task.task_id
    evidence_directory.mkdir()
    row = {
        "task_id": task.task_id,
        "engine": "chromium",
        "attempt": 1,
        "is_retry": False,
        "qualification_run": True,
        "status": "pass",
        "combined_status": "pass",
        "process_status": "pass",
        "result_status": "pass",
        "evidence_status": "pass",
        "trace": [],
        "evidence_directory": str(evidence_directory),
        "task_contract_sha256": task_contract_sha256(_task_contract(task)),
    }
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        "".join(
            json.dumps(
                {
                    **row,
                    "run_id": f"run-{pass_index}",
                    "qualification_pass_index": pass_index,
                    "qualification_required_passes": 2,
                }
            )
            + "\n"
            for pass_index in (1, 2)
        ),
        encoding="utf-8",
    )

    result = build_repair_queue(TaskSuite("lex_browser_use_tasks/4", (task,)), attempts)

    assert result["tasks"][0]["disposition"] == "needs_trace_repair"
    assert result["tasks"][0]["semantic_conflicts"] == [
        "new_branch_required_but_main_branch_selected"
    ]
    assert result["tasks"][0]["adjudication"] is None


def test_repaired_task_cannot_be_eligible_without_validated_attempt():
    suite = {
        "benchmark": {},
        "tasks": [{"id": "x", "ground_truth": {"qualification": {"status": "pending"}}}],
    }
    qualification = {
        "schema": QUALIFICATION_SCHEMA,
        "tasks": [
            {
                "task_id": "x",
                "disposition": "needs_trace_repair",
                "adjudication": {"status": "eligible", "reason": "repair validated"},
            }
        ],
    }

    with pytest.raises(ValueError, match="exactly two validated_attempt_paths"):
        apply_adjudication(suite, qualification)


def test_unchanged_passing_task_still_requires_bound_qualification_attempt():
    suite = {
        "benchmark": {},
        "tasks": [{"id": "x", "ground_truth": {"qualification": {"status": "pending"}}}],
    }
    qualification = {
        "schema": QUALIFICATION_SCHEMA,
        "tasks": [
            {
                "task_id": "x",
                "disposition": "eligible",
                "adjudication": {"status": "eligible"},
            }
        ],
    }

    with pytest.raises(ValueError, match="exactly two validated_attempt_paths"):
        apply_adjudication(suite, qualification)


def test_legacy_one_pass_qualification_schema_cannot_publish_eligibility():
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
    qualification = {
        "schema": "lex_browser_use_qualification/1",
        "tasks": [
            {
                "task_id": "x",
                "disposition": "eligible",
                "adjudication": {
                    "status": "eligible",
                    "validated_attempt_path": "/legacy/attempt.json",
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="qualification schema"):
        apply_adjudication(suite, qualification)


def test_task_contract_binds_verified_evaluator_source_contract():
    task = {
        "id": "x",
        "title": "Task",
        "trajectory": {"actions": [{"action": "click"}]},
        "source": {
            "verified_evaluators": [
                {"evaluator": "AgentResponseEvaluator", "expected": {"value": 1}}
            ]
        },
    }
    changed = json.loads(json.dumps(task))
    changed["source"]["verified_evaluators"][0]["expected"]["value"] = 2

    assert task_contract_sha256(task) != task_contract_sha256(changed)


def test_two_validated_attempts_require_distinct_run_ids(tmp_path):
    task = {
        "id": "x",
        "title": "Task",
        "trajectory": {"actions": [{"action": "click"}]},
        "ground_truth": {},
    }
    paths = [
        _write_validated_attempt(
            tmp_path,
            task,
            pass_index=pass_index,
            run_id=f"run-{pass_index}",
        )[1]
        for pass_index in (1, 2)
    ]
    second = json.loads(paths[1].read_text(encoding="utf-8"))
    second["run_id"] = "run-1"
    paths[1].write_text(json.dumps(second), encoding="utf-8")

    with pytest.raises(ValueError, match="distinct run_id"):
        validated_attempt_sha256s(
            {"validated_attempt_paths": [str(path) for path in paths]},
            "x",
            expected_contract_sha256=task_contract_sha256(task),
        )


def test_two_validated_attempt_paths_must_be_unique(tmp_path):
    task = {
        "id": "x",
        "title": "Task",
        "trajectory": {"actions": [{"action": "click"}]},
        "ground_truth": {},
    }
    _, path = _write_validated_attempt(tmp_path, task, pass_index=1)

    with pytest.raises(ValueError, match="paths must be unique"):
        validated_attempt_sha256s(
            {"validated_attempt_paths": [str(path), str(path)]},
            "x",
            expected_contract_sha256=task_contract_sha256(task),
        )


def test_new_branch_task_cannot_qualify_a_main_branch_trajectory():
    task = {
        "title": "Commit the change to a new branch",
        "trajectory": {
            "actions": [
                {
                    "action": "click",
                    "target": {"by": "text", "value": "Commit to main branch"},
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ("new_branch_required_but_main_branch_selected",)


def test_semantic_audit_does_not_guess_when_main_branch_is_allowed():
    task = {
        "title": "Commit the updated title",
        "trajectory": {
            "actions": [
                {
                    "action": "click",
                    "target": {"by": "text", "value": "Commit to main branch"},
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ()


def test_declared_project_name_must_appear_in_project_name_fills():
    task = {
        "title": 'Start a private project called "web_agent_android_xl"',
        "trajectory": {
            "actions": [
                {
                    "action": "fill",
                    "target": {"by": "label", "value": "Project name"},
                    "text": "web_agent_android",
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ("required_project_name_not_filled",)


def test_project_name_audit_accepts_a_later_corrected_fill():
    task = {
        "title": 'Create a project named "expected-name"',
        "trajectory": {
            "actions": [
                {
                    "action": "fill",
                    "target": {"by": "label", "value": "Project name"},
                    "text": "initial-name",
                },
                {
                    "action": "fill",
                    "target": {"by": "label", "value": "Project name"},
                    "text": "expected-name",
                },
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ()


def test_declared_file_name_must_appear_as_the_filled_basename():
    task = {
        "title": "Create a folder and a file named urls.json inside it",
        "trajectory": {
            "actions": [
                {
                    "action": "fill",
                    "target": {"by": "placeholder", "value": "File name"},
                    "text": "cars/url.txt",
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ("required_file_name_not_filled",)


def test_file_name_audit_accepts_the_declared_basename_in_a_folder():
    task = {
        "title": "Create a file named urls.json",
        "trajectory": {
            "actions": [
                {
                    "action": "fill",
                    "target": {"by": "placeholder", "value": "File name"},
                    "text": "car/urls.json",
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ()


def test_network_evaluator_values_must_be_filled_into_their_owned_fields():
    task = {
        "title": "Create a forum post",
        "trajectory": {
            "actions": [
                {
                    "action": "fill",
                    "target": {"by": "label", "value": "Body"},
                    "text": "Expected title",
                },
                {
                    "action": "fill",
                    "target": {"by": "label", "value": "Title"},
                    "text": "Wrong title",
                },
            ]
        },
        "source": {
            "verified_evaluators": [
                {
                    "evaluator": "NetworkEventEvaluator",
                    "expected": {"post_data": {"submission[title]": "Expected title"}},
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ("evaluator_title_value_not_filled",)


def test_network_evaluator_field_audit_accepts_exact_owned_fill():
    task = {
        "title": "Create a forum post",
        "trajectory": {
            "actions": [
                {
                    "action": "fill",
                    "target": {"by": "label", "value": "Title"},
                    "text": "Expected title",
                }
            ]
        },
        "source": {
            "verified_evaluators": [
                {
                    "evaluator": "NetworkEventEvaluator",
                    "expected": {"post_data": {"submission[title]": "Expected title"}},
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ()


def test_network_evaluator_field_audit_does_not_guess_hidden_fields():
    task = {
        "title": "Subscribe the current account",
        "trajectory": {"actions": [{"action": "click", "selector": "#subscribe"}]},
        "source": {
            "verified_evaluators": [
                {
                    "evaluator": "NetworkEventEvaluator",
                    "expected": {"post_data": {"email": "account@example.com"}},
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ()


def test_network_evaluator_field_audit_detects_value_in_wrong_field():
    task = {
        "title": "Submit a repository URL",
        "trajectory": {
            "actions": [
                {
                    "action": "fill",
                    "target": {"by": "label", "value": "Body"},
                    "text": "http://old.example:8023/auth0/angular-storage",
                }
            ]
        },
        "source": {
            "verified_evaluators": [
                {
                    "evaluator": "NetworkEventEvaluator",
                    "expected": {
                        "post_data": {"submission[url]": "__GITLAB__/auth0/angular-storage"}
                    },
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ("evaluator_url_value_filled_in_wrong_field",)


def test_network_evaluator_url_audit_normalizes_legacy_origin_to_site_token():
    task = {
        "title": "Submit a repository URL",
        "trajectory": {
            "actions": [
                {
                    "action": "fill",
                    "target": {"by": "label", "value": "URL"},
                    "text": ("http://old.example:8023/auth0/angular-storage"),
                }
            ]
        },
        "source": {
            "verified_evaluators": [
                {
                    "evaluator": "NetworkEventEvaluator",
                    "expected": {
                        "post_data": {"submission[url]": "__GITLAB__/auth0/angular-storage"}
                    },
                }
            ]
        },
    }

    assert trajectory_semantic_conflicts(task) == ()


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
        "schema": QUALIFICATION_SCHEMA,
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
        "schema": QUALIFICATION_SCHEMA,
        "tasks": [
            {
                "task_id": "x",
                "disposition": "needs_trace_repair",
                "adjudication": {
                    "status": "eligible",
                    "reason": "replaced a stale selector",
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
    paths = [
        _write_validated_attempt(
            tmp_path,
            validation_task,
            pass_index=pass_index,
        )[1]
        for pass_index in (1, 2)
    ]
    qualification["tasks"][0]["adjudication"]["validated_attempt_paths"] = [
        str(path) for path in paths
    ]

    result = apply_adjudication(suite, qualification)

    task = result["tasks"][0]
    assert task["trajectory"]["actions"] == corrected["actions"]
    assert len(task["ground_truth"]["qualification"]["validated_run_sha256s"]) == 2
    assert "validated_attempt_paths" not in task["ground_truth"]["qualification"]
    assert task["trajectory"]["provenance"]["original_trajectory_sha256"]


def test_repaired_task_cannot_use_old_har_evidence(tmp_path):
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
    attempts = [
        _write_validated_attempt(
            tmp_path,
            suite["tasks"][0],
            pass_index=pass_index,
            har_version="1" if pass_index == 1 else HAR_CREATOR_VERSION,
        )[1]
        for pass_index in (1, 2)
    ]
    qualification = {
        "schema": QUALIFICATION_SCHEMA,
        "tasks": [
            {
                "task_id": "x",
                "disposition": "eligible",
                "adjudication": {
                    "status": "eligible",
                    "validated_attempt_paths": [str(path) for path in attempts],
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="HAR creator"):
        apply_adjudication(suite, qualification)


def test_incremental_apply_promotes_selected_task_without_revalidating_existing(tmp_path):
    existing = {
        "id": "existing",
        "trajectory": {
            "schema": "lex_browser_trajectory/1",
            "actions": [{"action": "click", "selector": "#existing"}],
        },
        "ground_truth": {"qualification": {"status": "eligible"}},
    }
    candidate = {
        "id": "candidate",
        "trajectory": {
            "schema": "lex_browser_trajectory/1",
            "actions": [{"action": "click", "selector": "#candidate"}],
        },
        "ground_truth": {"qualification": {"status": "pending"}},
    }
    attempts = [
        _write_validated_attempt(tmp_path, candidate, pass_index=pass_index)[1]
        for pass_index in (1, 2)
    ]
    qualification = {
        "schema": QUALIFICATION_SCHEMA,
        "tasks": [
            {
                "task_id": "candidate",
                "disposition": "eligible",
                "adjudication": {
                    "status": "eligible",
                    "validated_attempt_paths": [str(path) for path in attempts],
                },
            }
        ],
    }

    result = apply_adjudication(
        {"benchmark": {}, "tasks": [existing, candidate]},
        qualification,
        task_ids={"candidate"},
    )

    assert result["tasks"][0]["ground_truth"]["qualification"] == {"status": "eligible"}
    assert result["tasks"][1]["ground_truth"]["qualification"]["status"] == "eligible"
    assert result["benchmark"]["qualification"]["qualified_count"] == 2


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
        "schema": QUALIFICATION_SCHEMA,
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
        "schema": QUALIFICATION_SCHEMA,
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


@pytest.mark.parametrize(
    "kind",
    [
        "observed_json_zip",
        "observed_json_filter_zip",
        "observed_ranked_labels",
        "observed_review_authors_matching",
    ],
)
def test_evidence_revalidation_accepts_supported_structured_observation_builders(kind):
    suite = {
        "benchmark": {},
        "tasks": [
            {
                "id": "corrected",
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "actions": [{"action": "click"}],
                },
                "ground_truth": {"expected_actions": ["click"]},
            }
        ],
    }
    qualification = {
        "tasks": [
            {
                "task_id": "corrected",
                "adjudication": {
                    "status": "eligible",
                    "response_builder": {"kind": kind},
                },
            }
        ]
    }

    result = build_evidence_revalidation_suite(suite, qualification)

    assert result["tasks"][0]["ground_truth"]["response_builder"] == {"kind": kind}


def test_skipped_task_requires_public_reason():
    suite = {
        "benchmark": {},
        "tasks": [{"id": "x", "ground_truth": {"qualification": {"status": "pending"}}}],
    }
    qualification = {
        "schema": QUALIFICATION_SCHEMA,
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
