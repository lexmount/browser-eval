from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.browser_use.repair_planner import (
    build_repair_packet,
    build_responses_request,
    candidate_to_proposal,
    planner_record,
)


def _suite():
    return {
        "tasks": [
            {
                "id": "webarena-1",
                "title": "Read the result",
                "environment": {"kind": "webarena_verified", "sites": ["shopping"]},
                "trajectory": {"actions": [{"action": "click", "selector": "#old"}]},
                "source": {
                    "verified_evaluators": [
                        {
                            "evaluator": "AgentResponseEvaluator",
                            "results_schema": {"type": "array", "items": {"type": "string"}},
                            "expected": {"retrieved_data": ["secret expected answer"]},
                        }
                    ]
                },
            }
        ]
    }


def _attempt():
    return {
        "task_id": "webarena-1",
        "engine": "chromium",
        "is_retry": False,
        "status": "fail",
        "process_status": "fail",
        "result_status": "fail",
        "trace": [
            {
                "index": 1,
                "action": "click",
                "arguments": {"selector": "#old"},
                "status": "fail",
                "error": "TimeoutError",
            }
        ],
    }


def test_repair_packet_excludes_expected_answer_and_keeps_output_schema(tmp_path):
    packet = build_repair_packet(_suite(), _attempt(), tmp_path)

    encoded = json.dumps(packet)
    assert "secret expected answer" not in encoded
    assert packet["task"]["evaluator_output_schema"][0]["results_schema"]["type"] == "array"


def test_repair_request_fixes_astra_low_and_can_bind_screenshot(tmp_path):
    screenshot = tmp_path / "final.png"
    screenshot.write_bytes(b"png")
    request = build_responses_request(
        build_repair_packet(_suite(), _attempt(), tmp_path), screenshot_path=screenshot
    )

    assert request["model"] == "gpt-6-astra"
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is False
    assert request["text"]["format"]["type"] == "json_schema"
    assert request["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")


def test_candidate_conversion_fails_closed_and_emits_probe_proposal():
    candidate = {
        "mode": "replace_actions",
        "actions_json": json.dumps(
            [
                {"action": "snapshot", "selector": "body", "save_as": "page"},
            ]
        ),
        "response_builder_json": json.dumps(
            {
                "kind": "observed_shape",
                "task_type": "retrieve",
                "shape": [{"$output": "page"}],
            }
        ),
        "reason": "The old locator is absent; capture the visible result.",
    }

    proposal = candidate_to_proposal("webarena-1", candidate)

    assert proposal["schema"] == "lex_browser_use_repair_proposal/1"
    assert proposal["replace_actions"][0]["save_as"] == "page"
    with pytest.raises((ValueError, json.JSONDecodeError)):
        candidate_to_proposal("webarena-1", {**candidate, "actions_json": "not json"})


def test_planner_record_does_not_duplicate_embedded_image():
    request = build_responses_request(build_repair_packet(_suite(), _attempt(), Path(".")))
    request["input"][0]["content"].append(
        {"type": "input_image", "image_url": "data:image/png;base64,secret"}
    )
    record = planner_record(
        packet={"task_id": "webarena-1"},
        request_body=request,
        response={"id": "resp_1", "status": "completed", "model": "gpt-6-astra"},
    )

    assert "secret" not in json.dumps(record)
