from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena.har import HAR_CREATOR_NAME, HAR_CREATOR_VERSION
from harness.trajectory_replay.public_webarena.qualification import (
    QUALIFICATION_SCHEMA,
    finalize_adjudication,
    qualification_from_applied_suite,
    task_contract_sha256,
)


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


def _write_attempt(root, task, pass_index, *, statuses=None):
    evidence = root / f"run-{pass_index}" / str(task["id"])
    evidence.mkdir(parents=True)
    screenshot = evidence / "final.png"
    page = evidence / "final-page.json"
    screenshot.write_bytes(f"image-{pass_index}".encode())
    page.write_text(json.dumps({"pass": pass_index}), encoding="utf-8")
    (evidence / "network.har").write_text(
        json.dumps(
            {
                "log": {
                    "creator": {
                        "name": HAR_CREATOR_NAME,
                        "version": HAR_CREATOR_VERSION,
                    },
                    "entries": [],
                }
            }
        ),
        encoding="utf-8",
    )
    row = {
        "task_id": str(task["id"]),
        "engine": "chromium",
        "qualification_run": True,
        "qualification_pass_index": pass_index,
        "qualification_required_passes": 2,
        "run_id": f"run-{pass_index}",
        "is_retry": False,
        "attempt": 1,
        "process_status": "pass",
        "result_status": "pass",
        "evidence_status": "pass",
        "status": "pass",
        "combined_status": "pass",
        "evidence_directory": str(evidence),
        "task_contract_sha256": task_contract_sha256(task),
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
    (evidence / "attempt.json").write_text(json.dumps(row), encoding="utf-8")
    return row


def test_rehydrates_public_qualification_without_private_attempt_paths():
    eligible = _task("webarena-1")
    eligible["ground_truth"]["qualification"] = {
        "status": "eligible",
        "validated_run_sha256s": ["a" * 64, "b" * 64],
    }
    skipped = _task("webarena-2")
    skipped["ground_truth"]["qualification"] = {
        "status": "skipped",
        "reason": "Known broken trace.",
    }

    result = qualification_from_applied_suite({"tasks": [eligible, skipped]})

    assert result["schema"] == QUALIFICATION_SCHEMA
    assert result["tasks"][0]["adjudication"] == eligible["ground_truth"]["qualification"]
    assert result["tasks"][1]["adjudication"] == skipped["ground_truth"]["qualification"]


def test_overlays_only_triple_pass_and_skips_every_unresolved_task(tmp_path):
    original = _task("webarena-1")
    unresolved = _task("webarena-2")
    repaired = _task("webarena-1", "fixed")
    repaired["ground_truth"]["qualification"] = {
        "status": "pending",
        "reason": "Exact corrected field.",
    }
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        "".join(
            json.dumps(_write_attempt(tmp_path, repaired, pass_index)) + "\n"
            for pass_index in (1, 2)
        ),
        encoding="utf-8",
    )
    qualification = {
        "schema": QUALIFICATION_SCHEMA,
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
    assert len(result["tasks"][0]["adjudication"]["validated_attempt_paths"]) == 2
    assert result["tasks"][0]["adjudication"]["corrected_trajectory"] == repaired["trajectory"]
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


def test_partial_validation_preserves_unobserved_eligible_task(tmp_path):
    existing = _task("webarena-1")
    candidate = _task("webarena-2")
    candidate["ground_truth"]["qualification"]["reason"] = "Validated repair."
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        "".join(
            json.dumps(_write_attempt(tmp_path, candidate, pass_index)) + "\n"
            for pass_index in (1, 2)
        ),
        encoding="utf-8",
    )
    qualification = {
        "schema": QUALIFICATION_SCHEMA,
        "tasks": [
            {
                "task_id": "webarena-1",
                "disposition": "eligible",
                "adjudication": {
                    "status": "eligible",
                    "validated_attempt_paths": ["/existing/pass-1", "/existing/pass-2"],
                },
            },
            {
                "task_id": "webarena-2",
                "disposition": "needs_trace_repair",
            },
        ],
    }

    result = finalize_adjudication(
        {"tasks": [existing, candidate]},
        qualification,
        [({"tasks": [candidate]}, attempts)],
        {},
    )

    assert result["tasks"][0]["adjudication"] == qualification["tasks"][0]["adjudication"]
    assert result["tasks"][1]["adjudication"]["status"] == "eligible"


def test_single_validated_pass_remains_skipped(tmp_path):
    task = _task("webarena-1")
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        json.dumps(_write_attempt(tmp_path, task, 1)) + "\n",
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

    assert result["schema"] == QUALIFICATION_SCHEMA
    assert result["tasks"][0]["adjudication"]["status"] == "skipped"


def test_later_two_pass_contract_supersedes_prior_failed_contract(tmp_path):
    original = _task("webarena-1")
    failed = _task("webarena-1", "failed-repair")
    passed = _task("webarena-1", "passed-repair")
    passed["ground_truth"]["qualification"]["reason"] = "Final validated correction."
    failed_attempts = tmp_path / "failed.jsonl"
    failed_attempts.write_text(
        "".join(
            json.dumps(
                _write_attempt(
                    tmp_path / "failed",
                    failed,
                    pass_index,
                    statuses={
                        "process_status": "fail",
                        "result_status": "fail",
                        "status": "fail",
                        "combined_status": "fail",
                    },
                )
            )
            + "\n"
            for pass_index in (1, 2)
        ),
        encoding="utf-8",
    )
    passed_attempts = tmp_path / "passed.jsonl"
    passed_attempts.write_text(
        "".join(
            json.dumps(_write_attempt(tmp_path / "passed", passed, pass_index)) + "\n"
            for pass_index in (1, 2)
        ),
        encoding="utf-8",
    )

    result = finalize_adjudication(
        {"tasks": [original]},
        {
            "tasks": [
                {"task_id": "webarena-1", "disposition": "needs_trace_repair"}
            ]
        },
        [
            ({"tasks": [failed]}, failed_attempts),
            ({"tasks": [passed]}, passed_attempts),
        ],
        {},
    )

    adjudication = result["tasks"][0]["adjudication"]
    assert adjudication["status"] == "eligible"
    assert adjudication["corrected_trajectory"] == passed["trajectory"]


def test_explicit_invalidation_wins_over_two_passing_runs(tmp_path):
    task = _task("webarena-1")
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        "".join(
            json.dumps(_write_attempt(tmp_path, task, pass_index)) + "\n" for pass_index in (1, 2)
        ),
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
        {"webarena-1": "Independent review found a task-contract defect."},
    )

    assert result["tasks"][0]["adjudication"] == {
        "status": "skipped",
        "reason": "Independent review found a task-contract defect.",
    }


def test_evidence_only_revalidation_does_not_invent_a_repair_reason(tmp_path):
    task = _task("webarena-1")
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        "".join(
            json.dumps(_write_attempt(tmp_path, task, pass_index)) + "\n" for pass_index in (1, 2)
        ),
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


def test_revalidation_preserves_existing_repair_reason(tmp_path):
    original = _task("webarena-1")
    repaired = _task("webarena-1", "fixed")
    attempts = tmp_path / "attempts.jsonl"
    attempts.write_text(
        "".join(
            json.dumps(_write_attempt(tmp_path, repaired, pass_index)) + "\n"
            for pass_index in (1, 2)
        ),
        encoding="utf-8",
    )

    result = finalize_adjudication(
        {"tasks": [original]},
        {
            "tasks": [
                {
                    "task_id": "webarena-1",
                    "disposition": "needs_trace_repair",
                    "adjudication": {
                        "status": "eligible",
                        "reason": "Previously validated correction.",
                    },
                }
            ]
        },
        [({"tasks": [repaired]}, attempts)],
        {},
    )

    assert result["tasks"][0]["adjudication"]["reason"] == ("Previously validated correction.")


@pytest.mark.parametrize("projection_passes", [False, True])
def test_finalization_rejects_projection_verdict_drift_in_both_directions(
    tmp_path, projection_passes,
):
    task = _task("webarena-1")
    rows = [_write_attempt(tmp_path, task, index) for index in (1, 2)]
    if projection_passes:
        source = Path(rows[0]["evidence_directory"]) / "attempt.json"
        raw = json.loads(source.read_text())
        raw["status"] = "fail"
        source.write_text(json.dumps(raw))
    else:
        rows[0]["status"] = "fail"
    projection = tmp_path / "attempts.jsonl"
    projection.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="projection differs"):
        finalize_adjudication(
            {"tasks": [task]}, {"tasks": [{"task_id": task["id"], "disposition": "eligible"}]},
            [({"tasks": [task]}, projection)], {},
        )


@pytest.mark.parametrize("field", ["title", "evaluator", "environment", "source"])
def test_finalization_rejects_requalified_changes_to_immutable_task_fields(tmp_path, field):
    original = _task("webarena-1")
    candidate = deepcopy(original)
    candidate[field] = "Different instruction" if field == "title" else {"changed": True}
    if field == "source":
        candidate[field] = {"verified_evaluators": [{"expected": "different oracle"}]}
    projection = tmp_path / "attempts.jsonl"
    projection.write_text("".join(json.dumps(_write_attempt(tmp_path, candidate, index)) + "\n"
                                  for index in (1, 2)))
    with pytest.raises(ValueError, match="immutable"):
        finalize_adjudication(
            {"tasks": [original]}, {"tasks": [{"task_id": original["id"],
                                               "disposition": "needs_trace_repair"}]},
            [({"tasks": [candidate]}, projection)], {},
        )


@pytest.mark.parametrize("inventory", ["canonical", "decisions", "validated"])
def test_duplicate_task_inventory_cannot_be_silently_collapsed(tmp_path, inventory):
    task = _task("webarena-1")
    suite = {"tasks": [task]}
    decisions = {"tasks": [{"task_id": task["id"], "disposition": "eligible"}]}
    validated = {"tasks": [task]}
    projection = tmp_path / "attempts.jsonl"
    projection.write_text("".join(json.dumps(_write_attempt(tmp_path, task, index)) + "\n"
                                  for index in (1, 2)))
    target = {"canonical": suite, "decisions": decisions, "validated": validated}[inventory]
    target["tasks"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        finalize_adjudication(suite, decisions, [(validated, projection)], {})


def test_empty_candidate_cannot_gain_eligibility_from_passing_metadata(tmp_path):
    task = _task("webarena-1")
    task["trajectory"]["actions"] = []
    projection = tmp_path / "attempts.jsonl"
    projection.write_text("".join(json.dumps(_write_attempt(tmp_path, task, index)) + "\n"
                                  for index in (1, 2)))
    with pytest.raises(ValueError, match="non-empty trajectory"):
        finalize_adjudication(
            {"tasks": [task]}, {"tasks": [{"task_id": task["id"], "disposition": "eligible"}]},
            [({"tasks": [task]}, projection)], {},
        )


@pytest.mark.parametrize("hashes", [None, [], ["a" * 64], ["a" * 64, "a" * 64], ["bad", "b" * 64]])
def test_applied_eligibility_requires_two_retained_proof_hashes(hashes):
    task = _task("webarena-1")
    task["ground_truth"]["qualification"] = {"status": "eligible"}
    if hashes is not None:
        task["ground_truth"]["qualification"]["validated_run_sha256s"] = hashes
    with pytest.raises(ValueError, match="two.*hash"):
        qualification_from_applied_suite({"tasks": [task]})
