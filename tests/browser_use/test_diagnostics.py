from __future__ import annotations

from harness.browser_use.diagnostics import build_failure_groups, classify_failure


def test_rule_diagnosis_extracts_failed_tool_call_and_selector():
    diagnosis = classify_failure(
        "TimeoutError: locator was not visible",
        [
            {
                "index": 4,
                "action": "click",
                "arguments": {"selector": "#checkout"},
                "status": "fail",
                "error": "Timeout 5000ms waiting for visible selector",
            }
        ],
    )

    assert diagnosis["category"] == "交互目标超时/不可见"
    assert diagnosis["failed_step"] == 4
    assert diagnosis["failed_action"] == "click"
    assert diagnosis["selector"] == "#checkout"


def test_failure_groups_deduplicate_primary_and_retry_but_preserve_attempts():
    failure = {
        "error": "TimeoutError",
        "trace": [
            {
                "index": 2,
                "action": "click",
                "arguments": {"selector": ".next"},
                "status": "fail",
                "error": "Timeout waiting for visible selector",
            }
        ],
    }
    summary = {
        "by_task": {
            "catalog": {
                "title": "翻页目录",
                "complexity": "complex",
                "failures": {
                    "moli": [
                        {**failure, "attempt": 1, "is_retry": False},
                        {**failure, "attempt": 2, "is_retry": True},
                    ]
                },
            }
        }
    }

    groups = build_failure_groups(summary)

    assert len(groups) == 1
    assert groups[0]["task_id"] == "catalog"
    assert groups[0]["engine"] == "moli"
    assert groups[0]["attempts"] == [
        {"attempt": 1, "is_retry": False},
        {"attempt": 2, "is_retry": True},
    ]
