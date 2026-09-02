from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness.browser_use.report import (
    METRIC_EXPLANATIONS,
    METRICS,
    SOURCE_FILES,
    SOURCE_REF,
    SOURCE_REPOSITORY,
    _absolute_metric_cell,
    _annotate_source_marks,
    _appendix_page,
    _correctness_table,
    _detail_filename,
    _duration_ratio_audit,
    _metric_guide,
    _metric_tables,
    _ratio_metric_cell,
    _task_detail_body,
    write_latest_report,
)


def test_duration_audit_marks_each_independent_p95_without_empty_column():
    values = [
        {"task_id": f"task-{i}", "denominator": 100, "numerator": i * 10, "ratio": i / 10}
        for i in range(1, 26)
    ]
    summary = {
        "resource_comparison": {"paired_ratios": {"metrics": {"duration_ms": {"values": values}}}}
    }
    audit = _duration_ratio_audit(summary)
    assert "统计位置" not in audit
    assert "第 24/25 项" in audit
    assert audit.count('class="percentile-marker"') == 3
    for label in ("Chromium 耗时 P95", "Moli 耗时 P95", "比值 P95"):
        assert audit.count(f">{label}</span>") == 1
    rows = re.findall(r"<tr[^>]*>.*?</tr>", audit)
    for index, row in enumerate(rows[1:], 1):
        assert row.count("<td ") == 5
        for engine in ("chromium", "moli"):
            assert f'href="{_detail_filename(f"task-{index}", engine)}"' in row
        assert (">比值 P95</span>" in row) == (index == 24)
        assert (">Moli 耗时 P95</span>" in row) == (index == 24)
    assert 'class="duration-detail-link"' in audit
    assert _duration_ratio_audit({}) == ""


@pytest.mark.parametrize("ids", [["same", "same"], [""]])
def test_source_task_ids_must_be_nonempty_and_unique(tmp_path, ids):
    with pytest.raises(ValueError, match="Task IDs"):
        write_latest_report(tmp_path, manifest={"suite": [{"task_id": x} for x in ids]}, summary={})


def test_ids_join_suite_analysis_details_and_audit_index(tmp_path):
    task = {"task_id": "task-1", "title": "标题", "calls": [], "ground_truth": {}}
    attempts = [{"attempt": 1, "status": "fail"}, {"attempt": 2, "status": "pass"}]
    summary = {
        "by_task": {
            "task-1": {"title": "标题", "attempts": {"moli": attempts, "chromium": attempts}}
        }
    }
    write_latest_report(tmp_path, manifest={"run_id": "run-1", "suite": [task]}, summary=summary)
    analysis = (tmp_path / "browser-use-evaluation-latest-analysis.html").read_text()
    suite = (tmp_path / "browser-use-evaluation-latest-suite.html").read_text()
    detail = (tmp_path / _detail_filename("task-1", "moli")).read_text()
    index = json.loads((tmp_path / "browser-use-evaluation-latest-data.json").read_text())[
        "task_index"
    ]
    label = "任务 ID <code>task-1</code>"
    assert label in suite and label in analysis and label in detail
    chromium_detail = (tmp_path / _detail_filename("task-1", "chromium")).read_text()
    assert label in chromium_detail
    search = re.search(r'data-search="([^"]+)"', analysis).group(1)
    assert "task-1" in search
    assert detail.count('class="record-id"') == 1
    assert "样本 ID" not in detail and "执行 ID" not in detail
    assert suite.count('class="record-id"') == 1
    assert index[0]["task_id"] == "task-1"
    assert len(index) == 1 and len(index[0]["executions"]) == 2
    assert index[0]["executions"][0]["attempts"] == [1, 2]
    other_run = tmp_path / "other-run"
    write_latest_report(other_run, manifest={"run_id": "run-2", "suite": [task]}, summary=summary)
    assert label in (other_run / _detail_filename("task-1", "moli")).read_text()


def test_homepage_guide_covers_every_displayed_metric_once():
    guide = _metric_guide()
    keys = {key for _, metrics in METRICS for key, _, _, _ in metrics}
    assert keys == set(METRIC_EXPLANATIONS)
    assert guide.count('<details class="guide-group">') == 4
    assert not re.search(r"<details[^>]*\bopen\b", guide)
    for _, metrics in METRICS:
        for key, label, _, unit in metrics:
            assert guide.count(f'data-metric="{key}"') == 1
            assert label in guide and unit in guide
    for explanation in (
        "结果正确率",
        "结果与参考过程均正确",
        "100%",
        "200%",
        "逻辑核",
        "1,024",
        "MiB·s",
        "第 19 个",
        "一次任务的连续采样",
        "多个任务各自算出的数值",
        "不能用两个方案的 P95 直接相除",
        "不能证明内存泄漏",
        "不等于绝对最高瞬时值",
    ):
        assert explanation in guide
    assert "均为越低越好" not in _metric_tables({})


def test_resource_groups_preserve_all_tables_with_progressive_disclosure():
    tables = _metric_tables({})
    assert tables.count('<details class="resource-group"') == 3
    assert tables.count('<details class="resource-group" open>') == 1
    assert tables.count('<details class="paired-disclosure">') == 3
    assert tables.count("<table>") == 6
    for group, metrics in METRICS:
        assert tables.count(f"<caption>{group}绝对值</caption>") == 1
        assert tables.count(f"<caption>{group}逐任务 Moli / Chromium 配对比值</caption>") == 1
        for _, label, _, _ in metrics:
            assert label in tables


def test_reproduction_links_to_real_harness_sources_not_step_only_instructions():
    page = _appendix_page({})
    assert "复现步骤" not in page
    assert "获取复现源码" in page
    assert f'href="{SOURCE_REPOSITORY}/tree/{SOURCE_REF}"' in page
    for _, path, _ in SOURCE_FILES:
        assert Path(path).is_file()
        assert f'href="{SOURCE_REPOSITORY}/blob/{SOURCE_REF}/{path}"' in page
    assert "browser-use-evaluation-latest-data.json" in page


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

    assert path.name == "browser-use-evaluation-latest.md"
    assert path.exists()
    assert not (reports / "browser-use-evaluation-20260101.md").exists()
    rendered = (reports / "browser-use-evaluation-latest.html").read_text(encoding="utf-8")
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
    assert rendered.index('id="metric-guide"') < rendered.index("结论与产品建议")
    assert "不能用两个方案的 P95 直接相除" in rendered
    assert "结果重试恢复" not in rendered
    assert "首次结果正确" not in rendered
    assert (reports / "browser-use-evaluation-latest-suite.html").exists()
    assert (reports / "browser-use-evaluation-latest-method.html").exists()
    assert not (reports / "browser-use-evaluation-latest-results.html").exists()
    assert (reports / "browser-use-evaluation-latest-analysis.html").exists()
    assert (reports / "browser-use-evaluation-latest-appendix.html").exists()
    assert (reports / "browser-use-evaluation-latest-data.json").exists()
    assert "总体结果" not in rendered
    assert "第 1/5 页" in rendered
    suite = (reports / "browser-use-evaluation-latest-suite.html").read_text(encoding="utf-8")
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
    audit = json.loads((reports / "browser-use-evaluation-latest-data.json").read_text())
    assert "human_review" not in audit["ground_truth_validation"]
    method = (reports / "browser-use-evaluation-latest-method.html").read_text(encoding="utf-8")
    assert "browser-use-evaluation-latest.html#metric-guide" in method
    assert "RX = Receive" in rendered
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
    analysis = (reports / "browser-use-evaluation-latest-analysis.html").read_text(encoding="utf-8")
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
    assert "答案对照" in detail
    assert "unique-answer" in detail
    assert "未产生（过程失败）" in detail
    assert "未判定" in detail
    assert "LLM 根因假设" not in analysis
    assert "未启用 LLM" not in analysis
    assert "时序机制研究" in analysis
    homepage = (reports / "browser-use-evaluation-latest.html").read_text(encoding="utf-8")
    assert 'class="failure-shortcut"' not in homepage
    assert f'href="{detail_files[0].name}"' not in homepage
    assert "#provider=moli&amp;status=fail" in homepage
    assert 'id="execution-empty"' in analysis
    assert "new URLSearchParams(raw)" in analysis
    assert "document.querySelector(location.hash)" not in analysis
    assert "CDP screencast 下采样" not in homepage
    assert "失败定位与技术分析 →" in homepage
    assert "rAF 目标间隔为 16 ms" not in homepage
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
    assert body.count("<caption>答案对照</caption>") == 1
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
    rendered = (reports / "browser-use-evaluation-latest-suite.html").read_text(encoding="utf-8")
    links = re.findall(
        r'<strong>公网测试页面</strong><span><a[^>]+href="(https://[^\"]+)"',
        rendered,
    )
    assert len(links) == 30
    for link in links:
        assert "localhost" not in link
        assert "127.0.0.1" not in link
