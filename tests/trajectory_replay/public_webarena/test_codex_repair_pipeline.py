from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena.diagnostics import (
    _prompt,
    _write_immutable,
    build_packet,
    run_repairs,
)
from harness.trajectory_replay.public_webarena.qualification import build_proposal_suite


def _json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    secret = "REFERENCE ANSWER MUST NOT REACH CODEX"
    suite = {
        "schema": "lex_browser_use_tasks/4",
        "benchmark": {"id": "test"},
        "tasks": [
            {
                "id": "webarena-4",
                "title": "Read the result heading",
                "difficulty": "simple",
                "tags": ["public-benchmark", "retrieve"],
                "trajectory": {
                    "schema": "lex_browser_trajectory/1",
                    "provenance": {"kind": "official"},
                    "actions": [{"action": "click", "selector": "#result-link"}],
                },
                "assertions": [],
                "evaluator": {
                    "kind": "webarena_verified",
                    "task_id": 4,
                    "revision": 2,
                },
                "environment": {"kind": "webarena_verified", "sites": ["shop"]},
                "ground_truth": {
                    "oracle": "webarena_verified",
                    "expected_actions": ["click"],
                    "qualification": {"status": "pending"},
                },
                "source": {
                    "url": "https://example.test/4.trace.zip",
                    "verified_evaluators": [
                        {
                            "evaluator": "AgentResponseEvaluator",
                            "results_schema": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "expected": {
                                "task_type": "retrieve",
                                "status": "SUCCESS",
                                "retrieved_data": [secret],
                            },
                        },
                        {
                            "evaluator": "NetworkEventEvaluator",
                            "expected": {
                                "method": "POST",
                                "url": "__SHOPPING__/cart",
                                "post_data": {"product": "42"},
                            },
                        },
                    ],
                },
            }
        ],
    }
    suite_path = tmp_path / "suite.json"
    _json(suite_path, suite)

    evidence = tmp_path / "evidence" / "attempt-1"
    screenshot = evidence / "final.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"png")
    page = evidence / "final-page.json"
    _json(page, {"url": "https://example.test/result", "visible_text": "Observed"})
    _json(evidence / "network.har", {"log": {"entries": []}})
    attempt = {
        "task_id": "webarena-4",
        "engine": "chromium",
        "attempt": 1,
        "is_retry": False,
        "qualification_run": True,
        "status": "fail",
        "process_status": "pass",
        "result_status": "fail",
        "combined_status": "fail",
        "evidence_status": "pass",
        "evidence_directory": str(evidence.resolve()),
        "error": f"expected {secret}",
        "trace": [
            {"action": "click", "status": "pass", "output": "observed click"},
            {
                "action": "evaluate_result",
                "status": "fail",
                "checks": [{"expected": secret}],
            },
        ],
        "validation": [{"expected": secret}],
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
    attempt_path = evidence / "attempt.json"
    _json(attempt_path, attempt)
    return suite_path, attempt_path


def test_codex_repair_packet_redacts_answer_and_binds_all_evidence(tmp_path):
    suite_path, attempt_path = _fixture(tmp_path)

    packet = build_packet(suite_path, attempt_path)
    serialized = json.dumps(packet, ensure_ascii=False)

    assert "REFERENCE ANSWER MUST NOT REACH CODEX" not in serialized
    assert packet["task"]["evaluator_contracts"][0]["expected"] == {
        "task_type": "retrieve",
        "status": "SUCCESS",
    }
    assert packet["task"]["evaluator_contracts"][1]["expected"]["post_data"] == {"product": "42"}
    assert packet["bindings"]["suite_sha256"] == hashlib.sha256(suite_path.read_bytes()).hexdigest()
    assert (
        packet["bindings"]["attempt_sha256"]
        == hashlib.sha256(attempt_path.read_bytes()).hexdigest()
    )
    assert set(packet["bindings"]["final_page"]) == {"screenshot", "page"}
    assert (
        packet["bindings"]["network_har"]["sha256"]
        == hashlib.sha256((attempt_path.parent / "network.har").read_bytes()).hexdigest()
    )
    assert packet["qualification_effect"].startswith("none_")


def test_codex_repair_packet_is_immutable(tmp_path):
    suite_path, attempt_path = _fixture(tmp_path)
    output = tmp_path / "packet.json"
    packet = build_packet(suite_path, attempt_path)

    _write_immutable(output, packet)
    _write_immutable(output, packet)
    changed = {**packet, "purpose": "different"}
    with pytest.raises(ValueError, match="refusing to overwrite"):
        _write_immutable(output, changed)


def test_codex_repair_runner_rejects_evidence_drift_before_model_call(tmp_path):
    suite_path, attempt_path = _fixture(tmp_path)
    packet_path = tmp_path / "packet.json"
    _write_immutable(packet_path, build_packet(suite_path, attempt_path))
    (attempt_path.parent / "final-page.json").write_text("changed", encoding="utf-8")
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True

    with pytest.raises(ValueError, match="final_page evidence binding mismatch"):
        run_repairs(
            suite_path,
            [packet_path],
            tmp_path / "drift-run",
            max_workers=1,
            run_subprocess=fake_run,
        )
    assert called is False


def test_codex_repair_runner_records_exact_model_and_validates_proposal(tmp_path):
    suite_path, attempt_path = _fixture(tmp_path)
    packet_path = tmp_path / "packet.json"
    _write_immutable(packet_path, build_packet(suite_path, attempt_path))
    proposal = {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": "webarena-4",
        "reason": "Capture the visible heading reached by the recorded click.",
        "append_actions": [{"action": "get_text", "selector": "h1", "save_as": "heading"}],
        "response_builder": {
            "kind": "observed_shape",
            "task_type": "retrieve",
            "shape": [{"$output": "heading"}],
        },
    }
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output = Path(command[command.index("-o") + 1])
        output.write_text(json.dumps(proposal), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, '{"type":"done"}\n', "")

    output_root = tmp_path / "run"
    summary = run_repairs(
        suite_path,
        [packet_path],
        output_root,
        max_workers=1,
        run_subprocess=fake_run,
    )

    command, kwargs = calls[0]
    assert command[:5] == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
    ]
    assert command[command.index("-m") + 1] == "gpt-6-astra"
    assert "model_reasoning_effort='low'" in command
    assert command[command.index("-s") + 1] == "read-only"
    assert kwargs["timeout"] == 900
    assert summary["status"] == "complete"
    result = json.loads((output_root / "webarena-4" / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "proposal_valid"
    assert result["model"] == "gpt-6-astra"
    assert result["reasoning_effort"] == "low"
    assert result["qualification_effect"] == "none"

    with pytest.raises(FileExistsError):
        run_repairs(
            suite_path,
            [packet_path],
            output_root,
            max_workers=1,
            run_subprocess=fake_run,
        )


def test_codex_repair_runner_fails_closed_on_invalid_proposal(tmp_path):
    suite_path, attempt_path = _fixture(tmp_path)
    packet_path = tmp_path / "packet.json"
    _write_immutable(packet_path, build_packet(suite_path, attempt_path))

    def fake_run(command, **_kwargs):
        output = Path(command[command.index("-o") + 1])
        output.write_text(
            json.dumps(
                {
                    "schema": "lex_browser_use_repair_proposal/1",
                    "task_id": "webarena-4",
                    "reason": "Missing actions and response builder.",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    output_root = tmp_path / "invalid-run"
    summary = run_repairs(
        suite_path,
        [packet_path],
        output_root,
        max_workers=1,
        run_subprocess=fake_run,
    )

    assert summary["status"] == "incomplete"
    result = summary["tasks"][0]
    assert result["status"] == "proposal_invalid"
    assert "requires exactly one" in result["error"]
    assert not (output_root / "webarena-4" / "proposal.json").exists()
    assert result["qualification_effect"] == "none"


def test_codex_prompt_declares_the_actual_validator_contract(tmp_path):
    prompt = _prompt(tmp_path / "packet.json")

    assert '"replace_actions"' in prompt
    assert "append_actions" in prompt
    assert '"response_builder"' in prompt
    assert '"reason"' in prompt
    assert "Do not emit trajectory" in prompt
    assert "null_after_verified_path" in prompt
    assert "get_text, get_all_text, get_table_column, get_attribute" in prompt
    assert "There is no evaluate" in prompt
    assert "navigate requires an HTTP(S) url and save_as" in prompt


def test_codex_repair_suite_combines_validated_proposals(tmp_path):
    suite_path, _ = _fixture(tmp_path)
    proposal_path = tmp_path / "proposal.json"
    _json(
        proposal_path,
        {
            "schema": "lex_browser_use_repair_proposal/1",
            "task_id": "webarena-4",
            "reason": "Capture the visible result heading.",
            "append_actions": [
                {"action": "get_text", "selector": "h1", "save_as": "heading"}
            ],
            "response_builder": {
                "kind": "observed_shape",
                "task_type": "retrieve",
                "shape": [{"$output": "heading"}],
            },
        },
    )

    result = build_proposal_suite(
        json.loads(suite_path.read_text(encoding="utf-8")),
        [json.loads(proposal_path.read_text())], kind="codex_repair_validation",
    )

    assert [task["id"] for task in result["tasks"]] == ["webarena-4"]
    assert result["tasks"][0]["trajectory"]["actions"][-1]["save_as"] == "heading"
    assert result["tasks"][0]["ground_truth"]["qualification"] == {
        "status": "pending",
        "reason": "Capture the visible result heading.",
    }
    assert result["benchmark"]["codex_repair_validation"] == {
        "non_canonical": True,
        "task_count": 1,
        "task_ids": ["webarena-4"],
        "qualification_required_passes": 2,
        "proposal_effect": "none_until_both_qualification_passes_succeed",
    }


def test_codex_repair_suite_rejects_duplicate_task_proposals(tmp_path):
    suite_path, _ = _fixture(tmp_path)
    proposal = tmp_path / "proposal.json"
    _json(
        proposal,
        {
            "schema": "lex_browser_use_repair_proposal/1",
            "task_id": "webarena-4",
            "reason": "Capture the visible result heading.",
            "append_actions": [{"action": "snapshot", "save_as": "page"}],
            "response_builder": {
                "kind": "observed_shape",
                "task_type": "retrieve",
                "shape": [{"$output": "page"}],
            },
        },
    )

    with pytest.raises(ValueError, match="unique"):
        build_proposal_suite(
            json.loads(suite_path.read_text(encoding="utf-8")),
            [json.loads(proposal.read_text())] * 2, kind="codex_repair_validation",
        )


def _proposal():
    return {
        "schema": "lex_browser_use_repair_proposal/1", "task_id": "webarena-4",
        "reason": "Observe the visible heading.",
        "append_actions": [{"action": "get_text", "selector": "h1", "save_as": "heading"}],
        "response_builder": {"kind": "observed_shape", "task_type": "retrieve",
                             "shape": [{"$output": "heading"}]},
    }


def _packet_fixture(tmp_path):
    suite_path, attempt_path = _fixture(tmp_path)
    packet_path = tmp_path / "packet.json"
    _write_immutable(packet_path, build_packet(suite_path, attempt_path))
    return suite_path, attempt_path, packet_path


def test_authoring_job_cannot_read_reference_answer_from_declared_inputs(tmp_path):
    suite_path, attempt_path, packet_path = _packet_fixture(tmp_path)
    seen = []

    def fake_run(command, **kwargs):
        packet = json.loads((Path(command[command.index("-C") + 1]) / "packet.json").read_text())
        seen.append(packet)
        assert "provenance_references" not in packet
        assert set(packet["evidence_references"]) == {"screenshot", "final_page", "network_har"}
        author_input = json.dumps(packet)
        for path in packet["evidence_references"].values():
            author_input += Path(path).read_bytes().decode(errors="replace")
        assert "REFERENCE ANSWER MUST NOT REACH CODEX" not in author_input
        assert str(attempt_path) not in author_input
        assert "Do not open raw attempt files" in kwargs["input"]
        Path(command[command.index("-o") + 1]).write_text(json.dumps(_proposal()))
        return subprocess.CompletedProcess(command, 0, "", "")

    summary = run_repairs(suite_path, [packet_path], tmp_path / "run",
                          max_workers=1, run_subprocess=fake_run)
    assert len(seen) == 1
    assert summary["status"] == "complete"


@pytest.mark.parametrize("field", ["attempt", "evidence_references", "redactions", "purpose"])
def test_forged_packet_payload_is_rejected_before_launch(tmp_path, field):
    suite_path, _, packet_path = _packet_fixture(tmp_path)
    packet = json.loads(packet_path.read_text())
    if field == "attempt":
        packet[field]["process_trace"].append({"action": "snapshot", "output": "injected answer"})
    elif field == "evidence_references":
        packet[field]["extra"] = str(suite_path)
    elif field == "redactions":
        packet[field]["reason"] = "injected instructions"
    else:
        packet[field] = "injected instructions"
    _json(packet_path, packet)
    calls = []
    with pytest.raises(ValueError, match="sanitized payload mismatch"):
        run_repairs(suite_path, [packet_path], tmp_path / "run", max_workers=1,
                    run_subprocess=lambda *args, **kwargs: calls.append(args))
    assert not calls
    assert not (tmp_path / "run").exists()


def test_legacy_packet_with_raw_attempt_access_is_rejected(tmp_path):
    suite_path, attempt_path, packet_path = _packet_fixture(tmp_path)
    packet = json.loads(packet_path.read_text())
    packet["schema"] = "lex_webarena_codex_repair_packet/1"
    packet["evidence_references"]["attempt"] = str(attempt_path)
    _json(packet_path, packet)
    with pytest.raises(ValueError, match="schema"):
        run_repairs(suite_path, [packet_path], tmp_path / "run", max_workers=1)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("filename", ["packet.json", "final-page.json", "final.png", "network.har"])
def test_linked_repair_inputs_are_rejected_before_launch(tmp_path, filename):
    suite_path, attempt_path, packet_path = _packet_fixture(tmp_path)
    source = packet_path if filename == "packet.json" else attempt_path.parent / filename
    original = source.with_suffix(source.suffix + ".original")
    source.rename(original)
    source.symlink_to(original)
    with pytest.raises(ValueError, match="symlink|binding mismatch"):
        run_repairs(suite_path, [packet_path], tmp_path / "run", max_workers=1)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("payload", [[], None, {"schema": "lex_browser_use_repair_proposal/1",
                                              "task_id": "webarena-4", "reason": "Bad shape",
                                              "replace_actions": ["click"],
                                              "response_builder": {}}])
def test_malformed_model_output_keeps_terminal_result_and_logs(tmp_path, payload):
    suite_path, _, packet_path = _packet_fixture(tmp_path)

    def fake_run(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text(json.dumps(payload))
        return subprocess.CompletedProcess(command, 0, "events retained", "stderr retained")

    output = tmp_path / "run"
    summary = run_repairs(suite_path, [packet_path], output, max_workers=1,
                          run_subprocess=fake_run)
    assert summary["status"] == "incomplete"
    assert summary["tasks"][0]["status"] == "proposal_invalid"
    assert (output / "webarena-4/events.jsonl").read_text() == "events retained"
    assert (output / "webarena-4/stderr.log").read_text() == "stderr retained"
    assert json.loads((output / "webarena-4/result.json").read_text())["error"]


def test_changed_evidence_during_job_cannot_complete_batch(tmp_path):
    suite_path, attempt_path, packet_path = _packet_fixture(tmp_path)

    def fake_run(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text(json.dumps(_proposal()))
        (attempt_path.parent / "final-page.json").write_text("changed during job")
        return subprocess.CompletedProcess(command, 0, "", "")

    summary = run_repairs(suite_path, [packet_path], tmp_path / "run", max_workers=1,
                          run_subprocess=fake_run)
    assert summary["status"] == "incomplete"
    assert summary["changed_inputs"] == [str(attempt_path.parent / "final-page.json")]
    assert summary["qualification_effect"] == "none"


def test_repair_model_configuration_is_bound_to_command_and_receipts(tmp_path):
    suite_path, _, packet_path = _packet_fixture(tmp_path)

    def fake_run(command, **kwargs):
        assert command[command.index("-m") + 1] == "test-model"
        assert "model_reasoning_effort='high'" in command
        Path(command[command.index("-o") + 1]).write_text(json.dumps(_proposal()))
        return subprocess.CompletedProcess(command, 0, "", "")

    summary = run_repairs(suite_path, [packet_path], tmp_path / "run", max_workers=1,
                          model="test-model", reasoning_effort="high", run_subprocess=fake_run)
    assert summary["model"] == summary["tasks"][0]["model"] == "test-model"
    command = json.loads((tmp_path / "run/webarena-4/command.json").read_text())
    assert command["reasoning_effort"] == "high"


@pytest.mark.parametrize("kwargs", [{"max_workers": True}, {"timeout_seconds": True},
                                    {"model": ""}, {"reasoning_effort": "invalid"}])
def test_invalid_job_configuration_makes_no_output(tmp_path, kwargs):
    suite_path, _, packet_path = _packet_fixture(tmp_path)
    with pytest.raises(ValueError):
        run_repairs(suite_path, [packet_path], tmp_path / "run", **{"max_workers": 1, **kwargs})
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "entry", ["main_repair_probe", "main_repair_batch", "main_codex_repair_suite"],
)
def test_invalid_derived_suite_never_creates_or_overwrites_output(tmp_path, entry):
    from harness.trajectory_replay.public_webarena import qualification

    suite_path, _ = _fixture(tmp_path)
    proposal = _proposal()
    proposal["append_actions"] = [{"action": "nonexistent_action"}]
    proposal_path = tmp_path / "proposal.json"
    _json(proposal_path, proposal)
    output = tmp_path / "derived.json"
    args = ["--suite", str(suite_path), "--proposal", str(proposal_path), "--output", str(output)]
    with pytest.raises(ValueError):
        getattr(qualification, entry)(args)
    assert not output.exists()
    output.write_text("previous immutable evidence")
    with pytest.raises(ValueError):
        getattr(qualification, entry)(args)
    assert output.read_text() == "previous immutable evidence"


def test_worker_exception_retains_job_and_batch_receipts(tmp_path):
    suite_path, _, packet_path = _packet_fixture(tmp_path)

    def fake_run(*args, **kwargs):
        raise RuntimeError("synthetic transport failure")

    output = tmp_path / "run"
    summary = run_repairs(suite_path, [packet_path], output, max_workers=1,
                          run_subprocess=fake_run)
    assert summary["status"] == "incomplete"
    assert summary["tasks"][0]["status"] == "worker_failed"
    assert "synthetic transport failure" in summary["tasks"][0]["error"]
    assert json.loads((output / "summary.json").read_text())["status"] == "incomplete"
    assert json.loads((output / "webarena-4/result.json").read_text())["status"] == "worker_failed"


@pytest.mark.parametrize(
    "entry", ["main_repair_probe", "main_repair_batch", "main_codex_repair_suite"],
)
def test_valid_derived_suite_is_immutable_and_source_is_unchanged(tmp_path, entry):
    from harness.trajectory_replay.public_webarena import qualification
    from harness.trajectory_replay.public_webarena.suite import load_suite

    suite_path, _ = _fixture(tmp_path)
    source = suite_path.read_bytes()
    proposal = tmp_path / "proposal.json"
    _json(proposal, _proposal())
    output = tmp_path / "derived.json"
    args = ["--suite", str(suite_path), "--proposal", str(proposal), "--output", str(output)]
    getattr(qualification, entry)(args)
    load_suite(output)
    frozen = output.read_bytes()
    getattr(qualification, entry)(args)
    changed = _proposal()
    changed["reason"] = "A different evidence-based revision"
    _json(proposal, changed)
    with pytest.raises(ValueError, match="overwrite"):
        getattr(qualification, entry)(args)
    assert output.read_bytes() == frozen
    assert suite_path.read_bytes() == source


def test_deleted_input_keeps_terminal_incomplete_receipt(tmp_path):
    suite_path, attempt_path, packet_path = _packet_fixture(tmp_path)

    def fake_run(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text(json.dumps(_proposal()))
        (attempt_path.parent / "network.har").unlink()
        return subprocess.CompletedProcess(command, 0, "", "")

    summary = run_repairs(suite_path, [packet_path], tmp_path / "run", max_workers=1,
                          run_subprocess=fake_run)
    assert summary["status"] == "incomplete"
    assert summary["tasks"][0]["status"] == "input_changed"
    assert not (tmp_path / "run/webarena-4/proposal.json").exists()


def test_codex_proposal_cannot_bypass_observation_with_scripted_output(tmp_path):
    suite_path, _, packet_path = _packet_fixture(tmp_path)
    proposal = _proposal()
    proposal["append_actions"] = [{"action": "evaluate", "script": "return 'invented'",
                                    "save_as": "heading"}]

    def fake_run(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text(json.dumps(proposal))
        return subprocess.CompletedProcess(command, 0, "", "")

    summary = run_repairs(suite_path, [packet_path], tmp_path / "run", max_workers=1,
                          run_subprocess=fake_run)
    assert summary["status"] == "incomplete"
    assert summary["tasks"][0]["status"] == "proposal_invalid"
    assert "unsupported authoring action" in summary["tasks"][0]["error"]
