from __future__ import annotations

import json
import re
from pathlib import Path

from harness.trajectory_replay.report import (
    METRICS,
    _annotate_source_marks,
    _correctness_table,
    _failure_review,
    _method_page,
    _metric_tables,
    _results_page,
    _suite_page,
    _summary_page,
    _task_detail_body,
    write_latest_report,
)


def test_failure_review_separates_process_interruptions_from_final_evaluator_failures():
    rows, reviewed = _failure_review(
        {
            "by_task": {
                "interrupted": {
                    "attempts": {
                        "moli": [
                            {
                                "attempt": 1,
                                "status": "fail",
                                "result_status": "fail",
                                "process_status": "fail",
                                "error": "element is not visible",
                            }
                        ]
                    }
                },
                "evaluated": {
                    "attempts": {
                        "chromium": [
                            {
                                "attempt": 1,
                                "status": "fail",
                                "result_status": "fail",
                                "process_status": "pass",
                                "validation": [
                                    {
                                        "evaluator_name": "NetworkEventEvaluator",
                                        "status": "failure",
                                        "score": 0,
                                        "actual": [],
                                    }
                                ],
                            }
                        ]
                    }
                },
            },
            "failure_diagnostics": {
                "rule_engine": {
                    "groups": [
                        {
                            "task_id": "interrupted",
                            "engine": "moli",
                            "failed_action": "click",
                            "evidence": "element is not visible",
                            "attempts": [{"attempt": 1}],
                        }
                    ]
                }
            },
        }
    )

    assert reviewed[("interrupted", "moli")]["category"] == "页面交互中断"
    assert reviewed[("evaluated", "chromium")]["category"] == "最终验收不匹配"
    assert "HAR 中没有" in reviewed[("evaluated", "chromium")]["reason"]
    assert {row[0]: row[1] for row in rows} == {
        "页面交互中断": "1",
        "最终验收不匹配": "1",
    }


def test_comparisons_are_neutral_for_displayed_ties_and_missing_values():
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


def test_resource_tables_compare_all_engines_in_one_table():
    by_engine = {"chromium": {}, "moli": {}, "webkit": {}}
    ratios = {"moli": {}, "webkit": {}}
    for _, metrics in METRICS:
        for index, spec in enumerate(metrics, 1):
            by_engine["chromium"][spec.key] = {
                "p50": spec.divisor * (100 + index + 0.004),
                "p95": spec.divisor * (200 + index + 0.004),
                "mean": spec.divisor * (150 + index + 0.004),
            }
            by_engine["moli"][spec.key] = {
                "p50": spec.divisor * (55 + index + 0.004),
                "p95": spec.divisor * (320 + index + 0.004),
                "mean": spec.divisor * (90 + index + 0.004),
            }
            by_engine["webkit"][spec.key] = {
                "p50": spec.divisor * (75 + index + 0.004),
                "p95": spec.divisor * (250 + index + 0.004),
                "mean": spec.divisor * (70 + index + 0.004),
            }
            ratios["moli"][spec.key] = {"p50": 0.5, "p95": 1.5, "mean": 0.75}
            ratios["webkit"][spec.key] = {"p50": 0.8, "p95": 1.2, "mean": 0.6}
    rendered = _metric_tables(
        {
            "resource_comparison": {
                "by_engine": by_engine,
                "relative_ratios": {
                    "baseline_engine": "chromium",
                    "by_engine": ratios,
                },
            }
        }
    )
    assert '<th colspan="3">P50</th>' in rendered
    assert '<th colspan="3">P95</th>' in rendered
    assert rendered.count("<th>Webkit</th>") == len(METRICS) * 3
    assert "逐任务比值 · Moli / Chromium" in rendered
    assert "逐任务比值 · Webkit / Chromium" in rendered
    assert "先逐任务相除再汇总" in rendered
    assert "0.50×" in rendered
    assert "1.50×" in rendered
    assert 'class="better"' in rendered
    assert 'class="worse"' in rendered
    assert rendered.count('class="metric-policy ') == sum(
        len(metrics) for _, metrics in METRICS
    )
    assert 'class="metric-policy lower"' in rendered
    assert 'class="metric-policy neutral"' in rendered
    assert "观察项不判胜负" in rendered
    cpu_row = rendered.split("平均 CPU 占单核", 1)[1].split("</tr>", 1)[0]
    assert 'class="better"' not in cpu_row
    assert 'class="worse"' not in cpu_row
    ratio_cpu_row = rendered.split("平均 CPU 占单核 · 观察项", 1)[1].split("</tr>", 1)[0]
    assert 'class="better"' not in ratio_cpu_row
    assert 'class="worse"' not in ratio_cpu_row


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
                    "denominator_engine": "chromium",
                    "numerator_engine": "moli",
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
    assert "WebArena 固定轨迹回放评测" in rendered
    assert "公开人工轨迹的确定性重放" in rendered
    assert "Chromium 与 Moli 浏览器工具调用评测" not in rendered
    assert "结论摘要" in rendered
    assert 'lang="zh-CN"' in rendered
    assert "报告分页" in rendered
    for label in (
        "结论摘要",
        "结果分析",
        "评测集",
        "选型依据",
        "环境与方法",
        "复现与附录",
    ):
        assert label in rendered
    assert "结果正确率是默认 Provider 的第一决策指标" not in rendered
    assert "数字限于这 1 条冻结轨迹" in rendered
    results = (reports / "webarena-trajectory-replay-latest-results.html").read_text(
        encoding="utf-8"
    )
    assert rendered.index('<th colspan="2">P50</th>') < rendered.index('<th colspan="2">P95</th>')
    assert rendered.index('<th colspan="2">P95</th>') < rendered.index(
        '<th colspan="2">平均值</th>'
    )
    assert 'data-label="P50 · Chromium">100.00</td>' in rendered
    assert 'data-label="P50 · Moli" class="better">50.00</td>' in rendered
    assert 'data-label="P95 · Chromium" class="better">200.00</td>' in rendered
    assert 'data-label="P95 · Moli">300.00</td>' in rendered
    assert "逐任务比值 · Moli / Chromium" in rendered
    assert "0.50×" in rendered
    assert "9.90×" in rendered
    assert "0.80×" in rendered
    assert "边际分位数相除" not in rendered
    assert "任务正确率" in rendered
    assert "首页决策摘要" not in rendered
    assert "正确性与资源要分开决策" not in rendered
    assert "速度与资源" in rendered
    assert 'role="progressbar"' in rendered
    assert "一眼看懂这轮评测" not in rendered
    assert "结果与参考过程均正确" not in rendered
    assert "程序验收结果" in rendered
    assert (reports / "webarena-trajectory-replay-latest-suite.html").exists()
    assert (reports / "webarena-trajectory-replay-latest-research.html").exists()
    research = (reports / "webarena-trajectory-replay-latest-research.html").read_text(
        encoding="utf-8"
    )
    assert "端到端 browser-use 采用 Top 3" in research
    assert "工程适用性 Top 3" in research
    assert "不跨类排名" in research
    assert "Online-Mind2Web</h3><strong>2 家机构采用" in research
    assert "Mind2Web</h3><strong>3 家机构采用" not in research
    assert "原始 Mind2Web 是离线 HTML 和动作预测数据集" in research
    assert "这些用例不要求 AMD" in research
    assert "1 条轨迹进入正式任务分母" in research
    assert "19 条轨迹进入正式双浏览器分母" not in research
    assert (reports / "webarena-trajectory-replay-latest-method.html").exists()
    assert (reports / "webarena-trajectory-replay-latest-results.html").exists()
    assert not (reports / "webarena-trajectory-replay-latest-analysis.html").exists()
    assert (reports / "webarena-trajectory-replay-latest-appendix.html").exists()
    assert not (reports / "webarena-trajectory-replay-latest-review.html").exists()
    assert (reports / "webarena-trajectory-replay-latest-data.json").exists()
    assert 'class="card result-guide"' in results
    assert (
        'href="webarena-trajectory-replay-latest-results.html#task-comparison"' in results
    )
    assert 'href="#task-comparison"' not in results
    assert 'id="performance"' not in results
    assert "<h2>总体结果</h2>" not in results
    assert "<h2>浏览器组合对比</h2>" in results
    assert results.count('class="metric-module"') == 0
    assert rendered.count('class="metric-module"') == 3
    assert 'class="better">4/4' in rendered
    suite = (reports / "webarena-trajectory-replay-latest-suite.html").read_text(encoding="utf-8")
    assert "1</strong><span>公网任务" in suite
    assert "公网测试页面" in suite
    assert 'href="https://example.com"' in suite
    assert "navigate → get_text" in suite
    assert "heading = Example" in suite
    assert 'class="distribution-grid compact-two"' in suite
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
    assert "final_passes" not in audit["result_audit"]
    assert "final_failures" not in audit["result_audit"]
    method = (reports / "webarena-trajectory-replay-latest-method.html").read_text(encoding="utf-8")
    assert "RX 是接收，TX 是发送" in method
    assert "P50 即中位数" in method
    assert "核验结论" in method
    assert "选型依据" in research
    assert "数据集" in suite
    assert "候选评测集" in research
    assert 'class="benchmark-grid"' in research
    assert 'class="rank-grid"' in research
    assert 'data-category="end_to_end_browser_use"' in research
    assert 'data-category="computer_use"' in research
    assert 'data-category="gui_grounding"' in research
    assert 'data-category="web_research"' in research
    assert "失败日志审查" in results
    assert "Codex" in method
    assert "<table" in rendered
    assert "<pre># Browser Use Evaluation" not in rendered


def test_grouped_correctness_marks_only_the_higher_rate_as_winner():
    manifest = {
        "suite": [
            {
                "task_id": "one",
                "complexity": "simple",
                "environment": {"sites": ["shopping"]},
                "evaluator": {"kind": "webarena_verified"},
            },
            {
                "task_id": "two",
                "complexity": "simple",
                "environment": {"sites": ["shopping"]},
                "evaluator": {"kind": "webarena_verified"},
            },
        ]
    }
    summary = {
        "functional": {
            "chromium": {"tasks": 2, "result_first_passes": 2, "result_final_passes": 2},
            "moli": {"tasks": 2, "result_first_passes": 1, "result_final_passes": 1},
        },
        "by_complexity": {
            "simple": {
                "functional": {
                    "chromium": {"tasks": 2, "result_first_passes": 2, "result_final_passes": 2},
                    "moli": {"tasks": 2, "result_first_passes": 1, "result_final_passes": 1},
                }
            }
        },
        "by_task": {
            "one": {
                "attempts": {
                    "chromium": [{"result_status": "pass"}],
                    "moli": [{"result_status": "pass"}],
                }
            },
            "two": {
                "attempts": {
                    "chromium": [{"result_status": "pass"}],
                    "moli": [{"result_status": "fail"}],
                }
            },
        },
        "resource_comparison": {"intersection_attempts": 0},
    }
    rendered = _results_page(manifest, summary)
    assert rendered.count('class="better">2/2') == 2
    assert 'class="better">1/2' not in rendered
    assert "没有统一的操作数阈值" in rendered


def test_results_page_builds_task_matrix_and_filters_for_three_engines():
    engines = ("chromium", "moli", "webkit")
    manifest = {
        "suite": [
            {
                "task_id": "one",
                "complexity": "simple",
                "environment": {"sites": ["shopping"]},
                "evaluator": {"kind": "webarena_verified"},
            }
        ]
    }
    summary = {
        "functional": {
            engine: {"tasks": 1, "result_final_passes": int(engine != "webkit")}
            for engine in engines
        },
        "by_complexity": {},
        "by_task": {
            "one": {
                "title": "One",
                "attempts": {
                    engine: [{"result_status": "fail" if engine == "webkit" else "pass"}]
                    for engine in engines
                },
            }
        },
        "resource_comparison": {"intersection_results": 0, "by_engine": {}},
    }

    rendered = _results_page(manifest, summary)

    assert 'id="compare-a"' in rendered
    assert '<option value="webkit">Webkit</option>' in rendered
    assert '<input type="checkbox" value="webkit" checked>Webkit' in rendered
    assert "结果有分歧" in rendered
    assert "A 通过，其余未通过" in rendered
    assert "所选浏览器全部通过" in rendered
    assert "通过浏览器" in rendered
    assert "未通过浏览器" in rendered
    assert "Chromium · 通过" in rendered
    assert "Webkit · 未通过" in rendered
    assert "--engine-count" not in rendered


def test_summary_decisions_follow_reversed_results_and_resource_ratios():
    manifest = {
        "run_id": "reversed",
        "suite": [
            {"task_id": "one", "evaluator": {"kind": "webarena_verified"}},
            {"task_id": "two", "evaluator": {"kind": "webarena_verified"}},
        ],
    }
    summary = {
        "functional": {
            "chromium": {"tasks": 2, "result_final_passes": 1},
            "moli": {"tasks": 2, "result_final_passes": 2},
        },
        "resource_comparison": {
            "intersection_attempts": 1,
            "relative_ratios": {
                "baseline_engine": "moli",
                "by_engine": {"chromium": {"duration_ms": {"p50": 1.25}}},
            },
            "paired_ratios": {
                "metrics": {
                    "memory_mean_bytes": {"p50": 1.5},
                    "memory_retained_bytes": {"p50": 1.2},
                    "duration_ms": {"p50": 0.8, "p95": 0.7},
                    "network_tx_bytes": {"p50": 0.6},
                }
            },
        },
    }
    rendered = _summary_page(manifest, summary)
    assert "Moli 通过数最多" in rendered
    assert "速度、CPU、内存和网络另列，不合成总分" in rendered
    assert "参评浏览器 / Moli" in rendered
    assert "参评浏览器 / Chromium" not in rendered
    assert "Chromium 的可用范围更广" not in rendered


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
    results = (reports / "webarena-trajectory-replay-latest-results.html").read_text(
        encoding="utf-8"
    )
    assert "浏览器组合对比" in results
    assert "执行记录" in results
    assert 'class="comparison-row"' in results
    assert 'id="compare-relation"' in results
    assert 'value="a_pass_others_fail"' in results
    assert 'id="compare-engines"' in results
    assert "失败分析" in results
    assert "TRACE_ONLY_SEARCH_MARKER" in results
    assert "c.dataset.search" in results
    assert "逐任务对比" not in results
    assert '<details class="metric-module" open>' not in results
    detail_files = list(reports.glob("*-task-*.html"))
    assert len(detail_files) == 1
    assert f'href="{detail_files[0].name}"' in results
    assert "webarena-trajectory-replay-latest-task-7461736b-6368726f6d69756d.html" not in results
    detail = detail_files[0].read_text()
    assert "<details" not in detail
    assert 'href="#' not in detail
    assert f'href="{detail_files[0].name}#case-steps"' in detail
    assert detail.count("<h1>") == 1
    assert 'class="pager"' not in detail
    assert "FIRST FULL STACK TRACE" not in detail
    assert detail.count("SECOND FULL STACK TRACE") == 1
    assert "参考操作与执行对照" in detail
    assert 'class="record-grid-head"' in results
    assert 'class="execution-summary"' in results
    assert 'data-label="程序验收"' in results
    assert 'data-label="工具调用"' in results
    assert 'data-label="轨迹执行耗时"' in results
    assert 'class="execution-body"' not in results
    assert 'class="failure-grid"' not in results
    assert 'class="failure-summary"' not in results
    assert 'class="diagnosis-panel"' in detail
    assert "Actionability probe" in detail
    assert "完整异常" in detail
    assert "BrowserTool 方法" in results
    assert 'id="f-status"' in results
    assert 'id="f-combined"' in results
    assert 'id="f-action"' in results
    assert 'id="f-query"' in results
    assert 'id="f-all"' in results
    assert 'id="f-attempt"' not in results
    assert '<option value="fail" selected>错误</option>' in results
    assert 'aria-live="polite"' in results
    assert "验收规则：预期、实际与判定" in detail
    assert "unique-answer" in detail
    assert "未产生（过程失败）" in detail
    assert "未判定" in detail
    assert "LLM 根因假设" not in results
    assert "未启用 LLM" not in results
    assert "根因边界" in results
    assert results.index("<h2>浏览器组合对比</h2>") < results.index(
        '<section class="card" id="failure-review">'
    ) < results.index('<section class="card" id="execution-results">')
    homepage = (reports / "webarena-trajectory-replay-latest.html").read_text(encoding="utf-8")
    assert 'class="failure-shortcut"' not in homepage
    assert f'href="{detail_files[0].name}"' not in homepage
    assert "#provider=moli&amp;status=fail" not in homepage
    assert 'id="execution-empty"' in results
    assert "new URLSearchParams(raw)" in results
    assert "document.querySelector(location.hash)" not in results
    assert "速度、CPU、内存和网络另列，不合成总分" in homepage
    assert results.count('class="execution-card"') == 1
    assert "采用结果" in detail
    assert "执行记录 1" not in detail
    assert "执行记录 2" not in detail
    assert "错误类别" in results
    assert "重试结论" not in results
    assert 'id="execution-task-moli"' in results
    assert ".case-detail .stackable table{min-width:0}" in detail


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
    method = _method_page(manifest, summary)

    assert "不能代表浏览器总体能力" in homepage
    assert "运行时没有 Agent 规划、模型调用或 LLM judge" in homepage
    assert "公开轨迹任务" in suite
    assert "自托管站点" in suite
    assert "自托管测试环境" in suite
    assert "官方轨迹来源" in suite
    assert "运行时没有 Agent 或模型选择下一步" in method
    assert "不用 LLM judge" in method


def test_method_page_derives_environment_and_prepare_counts_from_attempts():
    manifest = {
        "suite": [
            {
                "task_id": "webarena-1",
                "environment": {"kind": "webarena_verified"},
                "evaluator": {"kind": "webarena_verified"},
            }
        ],
        "protocol": {},
        "environment": {},
    }
    summary = {
        "by_task": {
            "webarena-1": {
                "attempts": {
                    "chromium": [
                        {
                            "environment_reset": {
                                "sites": {
                                    "shopping": {
                                        "reset": "container_recreated_from_pinned_image"
                                    }
                                }
                            },
                            "trace": [],
                        }
                    ],
                    "moli": [
                        {
                            "environment_reset": None,
                            "trace": [
                                {"action": "prepare_environment", "status": "fail"}
                            ],
                        }
                    ],
                }
            }
        }
    }

    method = _method_page(manifest, summary)

    assert "1 次镜像重建、0 次温复用、0 次 Map 健康检查、1 次无重置收据" in method
    assert "其中 1 次在轨迹开始前的环境准备阶段失败" in method


def test_all_published_suite_tasks_link_to_public_web_targets(tmp_path):
    suite_file = Path(__file__).resolve().parents[2] / "examples/trajectory_replay/tasks.v4.json"
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
    rendered = (reports / "webarena-trajectory-replay-latest-suite.html").read_text(
        encoding="utf-8"
    )
    links = re.findall(
        r'<strong>公网测试页面</strong><span><a[^>]+href="(https://[^\"]+)"',
        rendered,
    )
    assert len(links) == 30
    for link in links:
        assert "localhost" not in link
        assert "127.0.0.1" not in link
