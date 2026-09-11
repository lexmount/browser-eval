from __future__ import annotations

import json
import re
from pathlib import Path

from harness.browser_use.report import (
    METRICS,
    _absolute_metric_cell,
    _annotate_source_marks,
    _correctness_table,
    _method_page,
    _metric_tables,
    _ratio_metric_cell,
    _suite_page,
    _summary_page,
    _task_detail_body,
    write_latest_report,
)


def test_comparisons_are_neutral_for_displayed_ties_and_missing_values():
    assert "class=" not in _absolute_metric_cell(1.001, 1.002, 1)
    assert "class=" not in _ratio_metric_cell(1.004)
    assert "class=" not in _ratio_metric_cell(None)
    assert 'class="better"' in _ratio_metric_cell(0.8)
    assert 'class="worse"' in _ratio_metric_cell(1.2)
    tied = {
        engine: {"tasks": 2, "result_final_passes": 2, "combined_final_passes": 2}
        for engine in ("chromium", "moli")
    }
    assert 'class="better"' not in _correctness_table({"functional": tied})


def test_source_permalink_character_is_annotated_not_removed():
    from html import unescape

    original = "The Python Tutorial¶"
    rendered = _annotate_source_marks(original)
    assert unescape(re.sub(r"<[^>]+>", "", rendered)) == original
    assert 'title="原网页标题锚点字符 U+00B6"' in rendered
    assert _annotate_source_marks("plain title") == "plain title"


def test_each_provider_links_to_its_failure_filter_including_zero_failures():
    rendered = _correctness_table(
        {
            "functional": {
                "chromium": {"tasks": 30, "result_final_passes": 30, "combined_final_passes": 30},
                "moli": {"tasks": 30, "result_final_passes": 25, "combined_final_passes": 25},
            }
        }
    )
    assert rendered.count('class="provider-failure-link"') == 2
    assert "#provider=chromium&amp;status=fail" in rendered
    assert "#provider=moli&amp;status=fail" in rendered
    assert "chromium：查看 0 个失败任务" in rendered
    assert "moli：查看 5 个失败任务" in rendered
    assert "failure-shortcut" not in rendered


def test_resource_tables_separate_absolute_values_from_paired_ratios():
    by_engine = {"chromium": {}, "moli": {}}
    paired = {}
    for _, metrics in METRICS:
        for index, (key, _, divisor, _) in enumerate(metrics, 1):
            by_engine["chromium"][key] = {
                "p50": divisor * (100 + index + 0.004),
                "p95": divisor * (200 + index + 0.004),
                "mean": divisor * (150 + index + 0.004),
            }
            by_engine["moli"][key] = {
                "p50": divisor * (55 + index + 0.004),
                "p95": divisor * (320 + index + 0.004),
                "mean": divisor * (90 + index + 0.004),
            }
            paired[key] = {"p50": 0.55, "p95": 1.32, "mean": 0.72}
    rendered = _metric_tables(
        {"resource_comparison": {"by_engine": by_engine, "paired_ratios": {"metrics": paired}}}
    )
    assert '<th colspan="2">P50</th>' in rendered
    assert '<th colspan="2">P95</th>' in rendered
    assert "Moli / Chromium</th>" not in rendered
    assert "逐任务比值 P50" in rendered
    assert "0.55×" in rendered
    assert "1.32×" in rendered
    assert 'class="better">0.55×' in rendered
    assert 'class="worse">1.32×' in rendered


def test_report_uses_fixed_latest_filename_and_removes_legacy_versions(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "browser-use-evaluation-20260101.md").write_text("old", encoding="utf-8")

    path = write_latest_report(
        reports,
        manifest={
            "run_id": "run-1",
            "ground_truth_validation": {
                "validator": "Codex Desktop in-app Browser Tool",
                "human_review": {"status": "pending"},
                "automatic_browser_replay": {"passed": 1, "total": 1, "status": "pass"},
            },
            "environment": {"docker": "29"},
            "protocol": {"repeats": 3, "sample_interval_ms": 100},
            "suite": [
                {
                    "task_id": "example",
                    "title": "Example task",
                    "step_count": 2,
                    "complexity": "simple",
                    "tool_calls": [
                        {
                            "action": "navigate",
                            "url": "{fixture_origin}/core.html",
                            "save_as": "page",
                        },
                        {"action": "get_text", "selector": "h1", "save_as": "heading"},
                    ],
                    "ground_truth": {
                        "expected_outputs": {"heading": "Example"},
                        "expected_actions": ["navigate", "get_text"],
                    },
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
            "functional": {
                "chromium": {
                    "tasks": 4,
                    "result_first_passes": 4,
                    "combined_first_passes": 3,
                    "result_recovered": 0,
                    "combined_recovered": 1,
                    "result_final_passes": 4,
                    "combined_final_passes": 4,
                },
                "moli": {
                    "tasks": 4,
                    "result_first_passes": 2,
                    "combined_first_passes": 1,
                    "result_recovered": 1,
                    "combined_recovered": 1,
                    "result_final_passes": 3,
                    "combined_final_passes": 2,
                },
            },
            "by_complexity": {},
            "by_task": {},
            "repeat_drift": {},
            "resource_comparison": {
                "intersection_attempts": 0,
                "by_engine": {
                    "chromium": {
                        "duration_ms": {"p50": 100, "p95": 200, "mean": 150},
                        "cpu_ms": {"p50": 129.514},
                        "network_tx_bytes": {"p50": 6.794 * 1024},
                    },
                    "moli": {
                        "duration_ms": {"p50": 50, "p95": 300, "mean": 120},
                        "cpu_ms": {"p50": 71.884},
                        "network_tx_bytes": {"p50": 21.564 * 1024},
                    },
                },
                "paired_ratios": {
                    "metrics": {
                        "duration_ms": {"p50": 0.5, "p95": 9.9, "mean": 0.8},
                    }
                },
            },
        },
    )

    assert path.name == "webarena-trajectory-replay-latest.md"
    assert path.exists()
    assert not (reports / "browser-use-evaluation-20260101.md").exists()
    rendered = (reports / "webarena-trajectory-replay-latest.html").read_text(encoding="utf-8")
    assert "Chromium 与 Moli：WebArena 固定轨迹回放评测" in rendered
    assert "RPA 式公开人工轨迹重放" in rendered
    assert "Chromium 与 Moli 浏览器工具调用评测" not in rendered
    assert "结论与产品建议" in rendered
    assert 'lang="zh-CN"' in rendered
    assert "报告分页" in rendered
    assert "结果正确率是默认 Provider 的第一决策指标" not in rendered
    assert "评测集包含 1 个结果唯一的任务" in rendered
    assert rendered.index('<th colspan="2">P50</th>') < rendered.index('<th colspan="2">P95</th>')
    assert rendered.index('<th colspan="2">P95</th>') < rendered.index(
        '<th colspan="2">平均值</th>'
    )
    assert '<td>100.00</td><td class="better">50.00</td>' in rendered
    assert '<td class="better">200.00</td><td>300.00</td>' in rendered
    assert "9.90×" in rendered
    assert "边际分位数相除" in rendered
    assert "结果重试恢复" not in rendered
    assert "首次结果正确" not in rendered
    assert (reports / "webarena-trajectory-replay-latest-suite.html").exists()
    assert (reports / "webarena-trajectory-replay-latest-research.html").exists()
    research = (reports / "webarena-trajectory-replay-latest-research.html").read_text(
        encoding="utf-8"
    )
    assert "端到端 browser-use 采用 Top 3" in research
    assert "工程适用性 Top 3" in research
    assert "本报告把 benchmark 分为四类，不做跨类排名" in research
    assert "原始 Mind2Web 是离线 HTML 和动作预测数据集" in research
    assert "这些用例不要求 AMD" in research
    assert "1 条轨迹进入正式双浏览器分母" in research
    assert "19 条轨迹进入正式双浏览器分母" not in research
    assert (reports / "webarena-trajectory-replay-latest-method.html").exists()
    assert not (reports / "webarena-trajectory-replay-latest-results.html").exists()
    assert (reports / "webarena-trajectory-replay-latest-analysis.html").exists()
    assert (reports / "webarena-trajectory-replay-latest-appendix.html").exists()
    assert (reports / "webarena-trajectory-replay-latest-data.json").exists()
    assert "总体结果" not in rendered
    assert "第 1/6 页" in rendered
    suite = (reports / "webarena-trajectory-replay-latest-suite.html").read_text(encoding="utf-8")
    assert "1</strong><span>公网任务" in suite
    assert "公网测试页面" in suite
    assert 'href="https://example.com"' in suite
    assert "navigate → get_text" in suite
    assert "heading = Example" in suite
    assert suite.count('href="https://example.com"') == 1
    assert suite.count("navigate → get_text") == 1
    assert "机器判定答案" not in suite
    assert "评测可信度与解读边界" not in rendered
    for field in ("query", "difficulty", "site", "action", "reset", "count", "empty"):
        assert f'id="suite-{field}"' in suite
    assert 'class="card suite-task"' in suite
    assert 'data-search="任务01 任务 01 example' in suite
    for html_path in reports.glob("*.html"):
        assert "人工复核" not in html_path.read_text(encoding="utf-8")
    audit = json.loads((reports / "webarena-trajectory-replay-latest-data.json").read_text())
    assert "human_review" not in audit["ground_truth_validation"]
    method = (reports / "webarena-trajectory-replay-latest-method.html").read_text(encoding="utf-8")
    assert "RX = Receive" in method
    assert "P50 与中位数是同一个统计量" in method
    assert "Codex" in method
    assert "<table" in rendered
    assert "<pre># Browser Use Evaluation" not in rendered


def test_report_links_to_full_failures_and_tool_trace_on_detail_pages(tmp_path):
    reports = tmp_path / "reports"
    write_latest_report(
        reports,
        manifest={
            "run_id": "failed",
            "protocol": {"primary_attempts": 1, "max_failure_retries": 1},
            "suite": [
                {
                    "task_id": "task",
                    "ground_truth": {"expected_outputs": {"answer": "unique-answer"}},
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
                                "error": "SECOND FULL STACK TRACE",
                                "trace": [
                                    {
                                        "index": 1,
                                        "action": "navigate",
                                        "status": "fail",
                                        "duration_ms": 10,
                                        "arguments": {"url": "https://example.com"},
                                        "output": "TRACE_ONLY_SEARCH_MARKER",
                                        "error": "SECOND FULL STACK TRACE",
                                    }
                                ],
                            },
                        ],
                        "chromium": [],
                    },
                }
            },
        },
    )
    analysis = (reports / "webarena-trajectory-replay-latest-analysis.html").read_text(encoding="utf-8")
    assert "逐任务分析与原始证据" in analysis
    assert "TRACE_ONLY_SEARCH_MARKER" in analysis
    assert "c.dataset.search" in analysis
    assert "逐任务对比" not in analysis
    assert "<details" not in analysis
    detail_files = list(reports.glob("*-task-*.html"))
    assert len(detail_files) == 1
    assert f'href="{detail_files[0].name}"' in analysis
    detail = detail_files[0].read_text()
    assert "<details" not in detail
    assert 'href="#' not in detail
    assert f'href="{detail_files[0].name}#case-steps"' in detail
    assert detail.count("<h1>") == 1
    assert 'class="pager"' not in detail
    assert "FIRST FULL STACK TRACE" in detail
    assert detail.count("SECOND FULL STACK TRACE") == 1
    assert "参考操作与执行对照" in detail
    assert 'class="record-grid-head"' in analysis
    assert 'class="execution-summary"' in analysis
    assert 'class="execution-body"' not in analysis
    assert 'class="failure-grid"' not in analysis
    assert 'class="failure-summary"' not in analysis
    assert 'class="diagnosis-panel"' in detail
    assert "Actionability probe" in detail
    assert "完整异常" in detail
    assert "BrowserTool 方法" in analysis
    assert 'id="f-status"' in analysis
    assert 'id="f-combined"' in analysis
    assert 'id="f-action"' in analysis
    assert 'id="f-query"' in analysis
    assert 'id="f-all"' in analysis
    assert 'id="f-attempt"' not in analysis
    assert '<option value="fail" selected>错误</option>' in analysis
    assert 'aria-live="polite"' in analysis
    assert "验收规则：预期、实际与判定" in detail
    assert "unique-answer" in detail
    assert "未产生（过程失败）" in detail
    assert "未判定" in detail
    assert "LLM 根因假设" not in analysis
    assert "未启用 LLM" not in analysis
    assert "时序机制研究" in analysis
    homepage = (reports / "webarena-trajectory-replay-latest.html").read_text(encoding="utf-8")
    assert 'class="failure-shortcut"' not in homepage
    assert f'href="{detail_files[0].name}"' not in homepage
    assert "#provider=moli&amp;status=fail" in homepage
    assert 'id="execution-empty"' in analysis
    assert "new URLSearchParams(raw)" in analysis
    assert "document.querySelector(location.hash)" not in analysis
    assert "CDP screencast 下采样" in homepage
    assert "rAF 目标间隔为 16 ms" in homepage
    assert analysis.count('class="execution-card"') == 1
    assert "执行记录 1" in detail
    assert "执行记录 2" in detail
    assert "错误类别" in analysis
    assert "重试结论" not in analysis
    assert 'id="execution-task-moli"' in analysis


def test_detail_merges_attempts_and_deduplicates_raw_evidence():
    call = {"action": "fill", "selector": "#field", "text": "expected", "save_as": "answer"}
    attempt = {
        "status": "fail",
        "error": "FULL EXCEPTION <script>",
        "duration_ms": 10,
        "validation": [
            {"output": "answer", "expected": "expected", "actual": "wrong", "passed": False}
        ],
        "process_validation": {
            "expected_actions": ["fill"],
            "actual_actions": ["fill"],
            "passed": False,
        },
        "resource": {"cpu_ms": 2},
        "trace": [
            {
                "index": 1,
                "action": "fill",
                "arguments": {k: v for k, v in call.items() if k != "save_as"},
                "status": "fail",
                "duration_ms": 10,
                "output": "SHARED OUTPUT",
                "error": "FULL EXCEPTION <script>",
            }
        ],
    }
    body = _task_detail_body(
        "task",
        "Title",
        "moli",
        {
            "tool_calls": [call, {"action": "get_text", "selector": "h1"}],
            "ground_truth": {"expected_outputs": {"answer": "expected"}},
        },
        [{**attempt, "attempt": 1}, {**attempt, "attempt": 2}],
        None,
    )
    assert body.count("SHARED OUTPUT") == 1
    assert body.count("FULL EXCEPTION &lt;script&gt;") == 1
    assert "FULL EXCEPTION <script>" not in body
    assert body.count("<caption>验收规则：预期、实际与判定</caption>") == 1
    assert body.count("<caption>参考操作与执行对照</caption>") == 1
    assert body.count("<caption>资源对照</caption>") == 1
    assert body.count("未执行") == 2
    assert 'id="evidence-dialog"' in body
    assert 'aria-labelledby="evidence-title"' in body
    assert 'data-evidence="evidence-' in body
    assert "dialog.showModal()" in body
    assert "content.textContent=" in body
    assert "content.innerHTML" not in body
    assert "preventScroll:true" in body
    assert 'aria-label="实际浏览器动作">fill</code>' in body
    assert 'class="action-name">fill</code>' in body
    assert 'class="attempt-block"' not in body
    assert "<details" not in body
    targets = set(re.findall(r'id="([^"]+)"', body))
    assert all(target in targets for target in re.findall(r'href="#([^"]+)"', body))


def test_detail_preserves_success_and_divergent_actual_calls():
    body = _task_detail_body(
        "task",
        "Title",
        "chromium",
        {"tool_calls": [{"action": "click", "selector": "#expected"}]},
        [
            {
                "status": "pass",
                "trace": [
                    {
                        "action": "fill",
                        "status": "pass",
                        "arguments": {"selector": "#actual"},
                        "output": "0",
                    }
                ],
                "validation": [{"output": "extra", "expected": "0", "actual": "0", "passed": True}],
            }
        ],
        None,
    )
    assert 'aria-label="实际浏览器动作">fill</code>' in body
    assert "与参考动作不同" in body
    assert "#actual" in body
    assert "额外调用" not in body
    assert "extra" in body
    assert "未记录资源采样" in body
    assert 'class="ok">通过' in body


def test_detail_renders_webarena_evaluators_as_readable_rules():
    body = _task_detail_body(
        "webarena-521",
        "Subscribe",
        "chromium",
        {"tool_calls": [{"action": "click", "selector": "button"}]},
        [
            {
                "status": "fail",
                "validation": [
                    {
                        "evaluator_name": "AgentResponseEvaluator",
                        "status": "success",
                        "score": 1,
                        "expected": {"task_type": "mutate", "status": "success"},
                        "actual": {"task_type": "mutate", "status": "success"},
                    },
                    {
                        "evaluator_name": "NetworkEventEvaluator",
                        "status": "failure",
                        "score": 0,
                        "expected": {"http_method": "POST", "url": {"base_url": "/save"}},
                        "actual": [],
                        "assertions": [
                            {
                                "assertion_name": "missing_navigation_event",
                                "status": "failure",
                                "assertion_msgs": ["No matching network event found."],
                            }
                        ],
                    },
                ],
                "trace": [],
                "process_validation": {"passed": False},
            }
        ],
        None,
    )

    assert body.count("<td>AgentResponseEvaluator</td>") == 1
    assert body.count("<td>NetworkEventEvaluator</td>") == 1
    assert "核对任务类型、完成状态和需要返回的答案" in body
    assert "在 HAR 中核对请求 URL、方法、请求数据和响应条件" in body
    assert "No matching network event found." in body
    assert "实际：0 条记录" in body
    assert "完整预期" in body
    assert "完整实际值" in body
    assert "逐条断言" in body


def test_webarena_report_uses_self_hosted_trajectory_scope_language():
    manifest = {
        "run_id": "run",
        "suite": [
            {
                "task_id": "webarena-521",
                "title": "Subscribe",
                "complexity": "simple",
                "tool_calls": [{"action": "click", "selector": "button"}],
                "ground_truth": {"expected_actions": ["click"]},
                "source": {
                    "url": "https://example.test/official-trace.zip",
                    "verified_evaluators": [
                        {"evaluator": "AgentResponseEvaluator"},
                        {"evaluator": "NetworkEventEvaluator"},
                    ],
                },
                "evaluator": {"kind": "webarena_verified"},
                "environment": {
                    "kind": "webarena_verified",
                    "sites": ["shopping"],
                    "start_urls": ["__SHOPPING__"],
                },
            }
        ],
        "protocol": {"post_reset_settle_ms": 1500},
        "environment": {},
    }
    summary = {
        "functional": {
            "chromium": {"result_final_passes": 1, "tasks": 1},
            "moli": {"result_final_passes": 0, "tasks": 1},
        },
        "resource_comparison": {
            "intersection_attempts": 0,
            "by_engine": {},
            "paired_ratios": {"metrics": {}},
        },
    }

    homepage = _summary_page(manifest, summary)
    suite = _suite_page(manifest)
    method = _method_page(manifest)

    assert "不足以推出浏览器总体优劣" in homepage
    assert "不是 LLM Agent 排名" in homepage
    assert "公开轨迹任务" in suite
    assert "自托管站点" in suite
    assert "自托管测试环境" in suite
    assert "官方轨迹来源" in suite
    assert "没有 agent 或模型在运行时选择下一步" in method
    assert "不使用 LLM judge" in method


def test_all_published_suite_tasks_link_to_public_web_targets(tmp_path):
    suite_file = Path(__file__).resolve().parents[2] / "examples/browser_use/tasks.v4.json"
    raw_tasks = json.loads(suite_file.read_text(encoding="utf-8"))["tasks"]
    suite = [
        {
            **task,
            "task_id": task["id"],
            "complexity": task["difficulty"],
        }
        for task in raw_tasks
    ]
    reports = tmp_path / "reports"
    write_latest_report(
        reports,
        manifest={"run_id": "suite-links", "suite": suite},
        summary={"functional": {}, "resource_comparison": {"by_engine": {}}},
    )
    rendered = (reports / "webarena-trajectory-replay-latest-suite.html").read_text(encoding="utf-8")
    links = re.findall(
        r'<strong>公网测试页面</strong><span><a[^>]+href="(https://[^\"]+)"',
        rendered,
    )
    assert len(links) == 30
    for link in links:
        assert "localhost" not in link
        assert "127.0.0.1" not in link
