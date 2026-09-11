from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.build_webarena_codex_repair_suite import build_suite
from scripts.build_webarena_repair_packet import _write_immutable, build_packet
from scripts.run_webarena_codex_repairs import _prompt, run_repairs


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

    result = build_suite(
        json.loads(suite_path.read_text(encoding="utf-8")), [proposal_path]
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
        build_suite(json.loads(suite_path.read_text(encoding="utf-8")), [proposal, proposal])
