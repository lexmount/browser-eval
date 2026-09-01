from __future__ import annotations

from harness.browser_use.report import write_latest_report


def test_report_uses_fixed_latest_filename_and_removes_legacy_versions(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "browser-use-evaluation-20260101.md").write_text("old", encoding="utf-8")

    path = write_latest_report(
        reports,
        manifest={
            "run_id": "run-1",
            "environment": {"docker": "29"},
            "protocol": {"repeats": 3, "sample_interval_ms": 100},
            "suite": [
                {
                    "task_id": "example",
                    "title": "Example task",
                    "step_count": 2,
                    "complexity": "step_01_05",
                    "tool_calls": [
                        {"action": "navigate", "url": "https://example.com", "save_as": "page"},
                        {"action": "get_text", "selector": "h1", "save_as": "heading"},
                    ],
                    "assertions": [{"output": "heading", "contains": "Example"}],
                }
            ],
        },
        summary={
            "functional": {},
            "by_complexity": {},
            "by_task": {},
            "repeat_drift": {},
            "resource_comparison": {
                "intersection_attempts": 0,
                "by_engine": {},
            },
        },
    )

    assert path.name == "browser-use-evaluation-latest.md"
    assert path.exists()
    assert not (reports / "browser-use-evaluation-20260101.md").exists()
    rendered = (reports / "browser-use-evaluation-latest.html").read_text(encoding="utf-8")
    assert "结论与产品建议" in rendered
    assert 'lang="zh-CN"' in rendered
    assert "报告分页" in rendered
    assert (reports / "browser-use-evaluation-latest-suite.html").exists()
    assert (reports / "browser-use-evaluation-latest-method.html").exists()
    assert (reports / "browser-use-evaluation-latest-results.html").exists()
    assert (reports / "browser-use-evaluation-latest-analysis.html").exists()
    assert (reports / "browser-use-evaluation-latest-appendix.html").exists()
    method = (reports / "browser-use-evaluation-latest-method.html").read_text(encoding="utf-8")
    assert "RX = Receive" in method
    assert "P50 与中位数是同一个统计量" in method
    assert "LiteLLM" in method
    assert "Codex" in method
    assert "<table" in rendered
    assert "<pre># Browser Use Evaluation" not in rendered


def test_report_renders_full_failures_and_tool_trace_in_collapsible_details(tmp_path):
    reports = tmp_path / "reports"
    write_latest_report(
        reports,
        manifest={
            "run_id": "failed",
            "protocol": {"primary_attempts": 1, "max_failure_retries": 1},
            "suite": [
                {
                    "task_id": "task",
                    "ground_truth": {
                        "expected_outputs": {"answer": "unique-answer"}
                    },
                }
            ],
        },
        summary={
            "functional": {},
            "by_complexity": {},
            "repeat_drift": {},
            "resource_comparison": {"intersection_attempts": 0, "by_engine": {}},
            "by_task": {
                "task": {
                    "title": "Task",
                    "functional": {},
                    "failures": {
                        "moli": [
                            {
                                "attempt": 2,
                                "error": "FULL STACK TRACE",
                                "trace": [
                                    {
                                        "index": 1,
                                        "action": "navigate",
                                        "status": "fail",
                                        "duration_ms": 10,
                                        "arguments": {"url": "https://example.com"},
                                        "output": "",
                                        "error": "timeout",
                                    }
                                ],
                            }
                        ],
                        "chromium": [],
                    },
                }
            },
        },
    )
    results = (reports / "browser-use-evaluation-latest-results.html").read_text(encoding="utf-8")
    assert "<details" in results
    assert "FULL STACK TRACE" in results
    assert "完整执行轨迹" in results
    assert "BrowserTool 方法" in results
    assert 'id="f-status"' in results
    assert 'id="f-action"' in results
    assert 'id="f-query"' in results
    assert "ground truth 判定" in results
    assert "unique-answer" in results
    assert "未产生（过程失败）" in results
    assert "未判定" in results
