"""Exercise qualification command boundaries without browser or evaluator calls."""
from __future__ import annotations

import hashlib
import json

import pytest

from harness.trajectory_replay.public_webarena import qualification as q
from harness.trajectory_replay.public_webarena.har import HAR_CREATOR_NAME, HAR_CREATOR_VERSION


def _inputs(tmp_path):
    task = {
        "id": "one", "title": "Save the changed value", "difficulty": "simple", "tags": ["mutate"],
        "trajectory": {"schema": "lex_browser_trajectory/1",
                       "actions": [{"action": "click", "selector": "#save"}]},
        "ground_truth": {"expected_actions": ["click"], "qualification": {"status": "pending"}},
        "evaluator": {"kind": "webarena_verified", "task_id": 1, "revision": 1},
        "environment": {"kind": "webarena_verified", "sites": ["shopping"]},
        "source": {"url": "https://example.test/task"},
    }
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"schema": "lex_browser_use_tasks/4", "benchmark": {},
                                 "tasks": [task]}))
    decision = {"task_id": "one", "disposition": "needs_trace_repair", "adjudication": {
        "status": "eligible", "reason": "Use the current save control",
        "corrected_trajectory": {"schema": "lex_browser_trajectory/1",
                                 "actions": [{"action": "click", "selector": "#current-save"}]},
    }}
    adjudication = tmp_path / "qualification.json"
    adjudication.write_text(json.dumps({"schema": q.QUALIFICATION_SCHEMA, "tasks": [decision]}))
    return task, suite, adjudication


def _attempts(tmp_path, task):
    rows = []
    for index in (1, 2):
        directory = tmp_path / f"pass-{index}"
        directory.mkdir()
        (directory / "final.png").write_bytes(b"synthetic screenshot")
        (directory / "final-page.json").write_text('{"visible_text":"Saved"}')
        (directory / "network.har").write_text(json.dumps({"log": {
            "creator": {"name": HAR_CREATOR_NAME, "version": HAR_CREATOR_VERSION}, "entries": [],
        }}))
        row = {"task_id": task["id"], "engine": "chromium", "attempt": 1, "is_retry": False,
               "qualification_run": True, "qualification_pass_index": index,
               "qualification_required_passes": 2, "run_id": f"run-{index}",
               "task_contract_sha256": q.task_contract_sha256(task),
               "evidence_directory": str(directory),
               **{key: "pass" for key in ("status", "process_status", "result_status",
                                           "evidence_status", "combined_status")},
               "final_page_evidence": {"status": "complete", **{
                   key: {"path": name, "sha256": hashlib.sha256(
                       (directory / name).read_bytes()).hexdigest()}
                   for key, name in (("screenshot", "final.png"), ("page", "final-page.json"))
               }}}
        (directory / "attempt.json").write_text(json.dumps(row))
        rows.append(row)
    projection = tmp_path / "attempts.jsonl"
    projection.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return projection


@pytest.mark.parametrize("entry", [q.main_repair_validation_suite,
                                    q.main_evidence_revalidation_suite])
def test_pending_suite_commands_validate_before_write_and_preserve_old_output(tmp_path, entry):
    _, suite, adjudication = _inputs(tmp_path)
    original = suite.read_bytes()
    output = tmp_path / "derived.json"
    args = ["--suite", str(suite), "--qualification", str(adjudication), "--output", str(output)]
    entry(args)
    q.load_suite(output)
    frozen = output.read_bytes()
    entry(args)
    invalid = json.loads(adjudication.read_text())
    invalid["tasks"][0]["adjudication"]["corrected_trajectory"]["actions"] = [
        {"action": "nonexistent_action"},
    ]
    adjudication.write_text(json.dumps(invalid))
    with pytest.raises(ValueError):
        entry(args)
    assert output.read_bytes() == frozen
    output.unlink()
    with pytest.raises(ValueError):
        entry(args)
    assert not output.exists()
    assert suite.read_bytes() == original


def test_apply_command_preserves_input_and_rejects_output_replacement(tmp_path):
    _, suite, adjudication = _inputs(tmp_path)
    original = suite.read_bytes()
    decision = {"schema": q.QUALIFICATION_SCHEMA, "tasks": [{"task_id": "one", "adjudication": {
        "status": "skipped", "reason": "Source contract requires correction",
    }}]}
    adjudication.write_text(json.dumps(decision))
    output = tmp_path / "applied.json"
    args = ["--suite", str(suite), "--qualification", str(adjudication), "--output", str(output)]
    q.main_apply_adjudication(args)
    assert q.load_suite(output).tasks[0].ground_truth["qualification"]["status"] == "skipped"
    frozen = output.read_bytes()
    decision["tasks"][0]["adjudication"]["reason"] = "A different source defect"
    adjudication.write_text(json.dumps(decision))
    with pytest.raises(ValueError, match="overwrite"):
        q.main_apply_adjudication(args)
    assert output.read_bytes() == frozen and suite.read_bytes() == original


def test_queue_and_finalization_commands_bind_two_pass_evidence_and_preserve_outputs(tmp_path):
    task, suite, adjudication = _inputs(tmp_path)
    projection = _attempts(tmp_path, task)
    queue = tmp_path / "queue.json"
    queue_args = ["--suite", str(suite), "--attempts", str(projection), "--output", str(queue)]
    q.main_repair_queue(queue_args)
    assert json.loads(queue.read_text())["tasks"][0]["adjudication"]["status"] == "eligible"
    result = tmp_path / "final.json"
    args = ["--suite", str(suite), "--qualification", str(queue), "--validated-run",
            f"{suite}::{projection}", "--output", str(result)]
    q.main_finalize_adjudication(args)
    assert json.loads(result.read_text())["tasks"][0]["adjudication"]["status"] == "eligible"
    frozen_queue, frozen_final = queue.read_bytes(), result.read_bytes()
    rows = [json.loads(line) for line in projection.read_text().splitlines()]
    rows[0]["status"] = "fail"
    projection.write_text("".join(json.dumps(row) + "\n" for row in rows))
    for entry, arguments in (
        (q.main_repair_queue, queue_args), (q.main_finalize_adjudication, args),
    ):
        with pytest.raises(ValueError, match="projection differs"):
            entry(arguments)
    assert queue.read_bytes() == frozen_queue and result.read_bytes() == frozen_final
