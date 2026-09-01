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
                    "complexity": "simple",
                    "tool_calls": [
                        {"action": "navigate", "url": "https://example.com", "save_as": "page"},
                        {"action": "get_text", "selector": "h1", "save_as": "heading"},
                    ],
                    "assertions": [{"output": "heading", "contains": "Example"}],
                    "source": {
                        "kind": "probe",
                        "scenario": "example",
                        "url": "https://example.com",
                        "license": "CC0",
                        "coverage": "静态标题读取",
                        "hypothesis": "静态内容应作为动态控件的对照组",
                    },
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
                    "by_engine": {
                        "chromium": {
                            "duration_ms": {"p50": 100, "p95": 200, "mean": 150},
                        },
                        "moli": {
                            "duration_ms": {"p50": 50, "p95": 300, "mean": 120},
                        },
                    },
                    "paired_ratios": {
                        "metrics": {
                            "duration_ms": {"p50": 0.5, "p95": 1.5, "mean": 0.8},
                        }
                    },
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
    assert "结果正确率是主指标" in rendered
    assert "评测集包含 1 个结果唯一的任务" in rendered
    assert rendered.index('<th colspan="3">P50</th>') < rendered.index('<th colspan="3">P95</th>')
    assert rendered.index('<th colspan="3">P95</th>') < rendered.index(
        '<th colspan="3">平均值</th>'
    )
    assert '<td class="better">50.00</td><td class="better">0.50×</td>' in rendered
    assert '<td class="better">200.00</td><td>300.00</td><td class="worse">1.50×</td>' in rendered
    assert '<td>150.00</td><td class="better">120.00</td><td class="better">0.80×</td>' in rendered
    assert (reports / "browser-use-evaluation-latest-suite.html").exists()
    assert (reports / "browser-use-evaluation-latest-method.html").exists()
    assert (reports / "browser-use-evaluation-latest-results.html").exists()
    assert (reports / "browser-use-evaluation-latest-analysis.html").exists()
    assert (reports / "browser-use-evaluation-latest-appendix.html").exists()
    assert (reports / "browser-use-evaluation-latest-data.json").exists()
    suite = (reports / "browser-use-evaluation-latest-suite.html").read_text(encoding="utf-8")
    assert "本轮共 1 个任务" in suite
    assert "覆盖范围：</strong>静态标题读取" in suite
    assert "待验证假设：</strong>静态内容应作为动态控件的对照组" in suite
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
            "failure_diagnostics": {
                "rule_engine": {
                    "groups": [
                        {
                            "task_id": "task",
                            "task_title": "Task",
                            "engine": "moli",
                            "category": "交互目标超时/不可见",
                            "failed_step": 1,
                            "failed_action": "navigate",
                            "selector": "body",
                            "evidence": "timeout",
                            "actionability_probe": {
                                "connected": True,
                                "visibility": "visible",
                            },
                            "attempts": [
                                {"attempt": 1, "is_retry": False},
                                {"attempt": 2, "is_retry": True},
                            ],
                        }
                    ]
                }
            },
            "by_task": {
                "task": {
                    "title": "Task",
                    "functional": {},
                    "failures": {
                        "moli": [
                            {
                                "attempt": 1,
                                "is_retry": False,
                                "status": "fail",
                                "result_status": "fail",
                                "process_status": "fail",
                                "combined_status": "fail",
                                "duration_ms": 9,
                                "error": "FIRST FULL STACK TRACE",
                                "trace": [],
                            },
                            {
                                "attempt": 2,
                                "is_retry": True,
                                "status": "fail",
                                "result_status": "fail",
                                "process_status": "fail",
                                "combined_status": "fail",
                                "duration_ms": 10,
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
    analysis = (reports / "browser-use-evaluation-latest-analysis.html").read_text(encoding="utf-8")
    assert "执行记录与原始证据" not in results
    assert "逐任务对比" not in results
    assert "逐任务结果与原始证据" in analysis
    assert "逐任务对比" not in analysis
    assert "<details" in analysis
    assert "FULL STACK TRACE" in analysis
    assert "完整执行轨迹" in analysis
    assert 'class="record-grid-head"' in analysis
    assert 'class="execution-summary"' in analysis
    assert 'class="execution-body"' in analysis
    assert 'class="failure-grid"' in analysis
    assert 'class="failure-summary"' in analysis
    assert "失败后实测 actionability probe" in analysis
    assert analysis.count("查看该失败的完整执行记录") == 1
    assert "BrowserTool 方法" in analysis
    assert 'id="f-status"' in analysis
    assert 'id="f-combined"' in analysis
    assert 'id="f-action"' in analysis
    assert 'id="f-query"' in analysis
    assert 'id="f-all"' in analysis
    assert 'id="f-attempt"' not in analysis
    assert '<option value="fail" selected>错误</option>' in analysis
    assert 'aria-live="polite"' in analysis
    assert "标准答案判定" in analysis
    assert "unique-answer" in analysis
    assert "未产生（过程失败）" in analysis
    assert "未判定" in analysis
    assert "LLM 根因假设" not in analysis
    assert "未启用 LLM" not in analysis
    assert "时序假设与放大性测试设计" in analysis
    assert "CDP screencast 帧的下采样节奏" in analysis
    assert "目标间隔为 16 ms" in analysis
    assert analysis.count('class="execution-card"') == 1
    assert "首次执行" in analysis
    assert "失败重试 1" in analysis
    assert "重试 1 次仍失败" in analysis
    assert 'href="#execution-task-moli"' in analysis
