from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from harness.trajectory_replay.public_webarena import diagnostics
from harness.trajectory_replay.public_webarena.diagnostics import build_failure_groups, classify_failure


@pytest.mark.parametrize(
    "error", [URLError("unavailable"), json.JSONDecodeError("bad", "", 0), KeyError("choices")]
)
def test_optional_llm_errors_preserve_rules_and_scores(monkeypatch, error):
    summary = {
        "score": {"moli": 0},
        "by_task": {"task": {"failures": {"moli": [{"error": "TimeoutError", "trace": []}]}}},
    }

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(diagnostics, "add_llm_diagnoses", fail)
    result = diagnostics.diagnose_summary(
        summary, use_llm=True, api_key="test", api_base="https://test.invalid"
    )
    assert result is summary
    assert summary["score"] == {"moli": 0}
    diagnosis = summary["failure_diagnostics"]
    assert len(diagnosis["rule_engine"]["groups"]) == 1
    assert diagnosis["llm"]["status"] == "error"
    assert diagnosis["llm"]["error_type"] == type(error).__name__
    assert diagnosis["llm"]["affects_scoring"] is False


def test_missing_llm_credentials_are_nonfatal(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    summary = diagnostics.diagnose_summary({}, use_llm=True)
    assert summary["failure_diagnostics"]["llm"]["error_type"] == "RuntimeError"


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


def test_navigation_timeout_is_not_misclassified_as_rendering_timeout():
    trace = [
        {
            "index": 1,
            "action": "navigate",
            "status": "fail",
            "arguments": {"url": "https://example.com/"},
            "error": "TimeoutError: page.goto exceeded 30000ms",
        }
    ]

    diagnosis = classify_failure(trace[0]["error"], trace)

    assert diagnosis["category"] == "导航/网络"


def test_capability_tags_do_not_override_navigation_failure_category():
    failure = {
        "error": "TimeoutError: page.goto exceeded 30000ms",
        "trace": [
            {
                "index": 1,
                "action": "navigate",
                "status": "fail",
                "arguments": {"url": "https://example.com/"},
                "error": "TimeoutError: page.goto exceeded 30000ms",
            }
        ],
        "attempt": 1,
        "is_retry": False,
    }
    summary = {
        "by_task": {
            "public-control": {
                "title": "公网控件",
                "complexity": "medium",
                "tags": ["native-control", "target"],
                "failures": {"moli": [failure]},
            }
        }
    }

    groups = build_failure_groups(summary)

    assert groups[0]["category"] == "导航/网络"
