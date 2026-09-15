import json
import zipfile
from pathlib import Path, PurePosixPath

import pytest

import harness.browser_use.report as report_module
from harness.browser_use.report import (
    _action_duration_cards,
    _attempt_table,
    _automatic_failure_type,
    _best,
    _common_final_success_resource_rows,
    _failure_diagnosis,
    _failure_scope_counts,
    _load_evidence_bundles,
    _logical_results,
    _paired_summary,
    _same_task_comparison,
    _summary_comparison_rows,
    _verification_html,
    build_report,
)
from harness.browser_use.report_i18n import localize_html
from harness.browser_use.selection import load_pilot
from scripts.check_browser_use_report import (
    FORBIDDEN_READER_TERMS,
    _check_execution_artifacts,
    _check_public_privacy,
    _check_reader_facing_ux,
)


def test_best_marks_only_one_visibly_distinct_winner() -> None:
    assert _best({"a": 1, "b": 1, "c": 2}, higher=False) == set()
    assert _best({"a": 1, "b": 2, "c": 3}, higher=False) == {"a"}
    assert (
        _best(
            {"a": 1000.0, "b": 1000.4},
            higher=False,
            display=lambda value: round(value / 1000, 3),
        )
        == set()
    )


def test_failure_scope_summary_does_not_merge_browser_evidence_into_agent() -> None:
    causes = {
        "a": {"subcategory": {"code": "agent_retrieval"}},
        "b": {"subcategory": {"code": "benchmark_evaluator"}},
        "c": {"subcategory": {"code": "evidence_capture"}},
    }

    assert [(label, count) for label, count, _ in _failure_scope_counts(causes)] == [
        ("Agent / 模型", 1),
        ("任务 / evaluator", 1),
        ("浏览器证据接口", 1),
    ]


def test_trace_screenshot_labels_count_only_publishable_frames(tmp_path: Path) -> None:
    trace = tmp_path / "trace.zip"
    image_payloads = {}
    frames = (("large-a", (1280, 720)), ("small", (320, 200)), ("large-b", (1280, 720)))
    for sha1, size in frames:
        buffer = report_module.BytesIO()
        report_module.Image.new("RGB", size, "white").save(buffer, format="JPEG")
        image_payloads[sha1] = buffer.getvalue()
    events = b"\n".join(
        json.dumps({"type": "screencast-frame", "sha1": sha1, "timestamp": index}).encode()
        for index, sha1 in enumerate(image_payloads, start=1)
    )
    with zipfile.ZipFile(trace, "w") as archive:
        archive.writestr("trace.trace", events)
        for sha1, payload in image_payloads.items():
            archive.writestr(f"resources/{sha1}", payload)

    screenshots = report_module._extract_trace_screenshots(trace, tmp_path / "public")

    assert [item["label"] for item in screenshots] == [
        "最后页面画面 1/2",
        "最后页面画面 2/2",
    ]
    assert [item["path"] for item in screenshots] == [
        "screenshots/frame-1.jpeg",
        "screenshots/frame-2.jpeg",
    ]


def test_failed_result_inventory_opens_exact_diagnosis_and_links_trajectory(
    tmp_path: Path,
) -> None:
    evaluation = tmp_path / "evaluation.json"
    events = tmp_path / "events.json"
    evaluation.write_text(
        json.dumps(
            {
                "score": 0,
                "evaluators_results": [
                    {
                        "evaluator_name": "AgentResponseEvaluator",
                        "score": 0,
                        "assertions": [
                            {"assertion_msgs": ["Array is missing 1 expected element(s)."]}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    events.write_text(
        json.dumps(
            [
                {
                    "type": "tool_execution_start",
                    "data": {
                        "tool_call_id": "1",
                        "tool_name": "browser",
                        "arguments": {"action": "click", "ref": "button-1"},
                    },
                },
                {
                    "type": "tool_execution_end",
                    "data": {
                        "tool_call_id": "1",
                        "status": "failure",
                        "error": "element detached",
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    attempt = {
        "model": "gpt-5.6-sol",
        "engine": "chromium",
        "task_id": 44,
        "tier": "easy",
        "attempt": 1,
        "score": 0,
        "paths": {"evaluation": str(evaluation), "events": str(events)},
    }
    tasks = [
        {
            "task_id": 44,
            "tier": "easy",
            "sites": ["shopping_admin"],
            "task_type": "RETRIEVE",
            "intent": "Inspect a report",
        }
    ]

    html = _attempt_table([attempt], tasks)

    for expected in (
        "查看诊断",
        '<dialog class="diagnosis-dialog"',
        "程序报错",
        "Array is missing 1 expected element(s).",
        "第 1 步 BrowserTool 执行异常",
        "失败动作",
        "click",
        "#trajectory-step-1",
        "#screenshots",
        "#runtime",
    ):
        assert expected in html


def test_english_localization_includes_visible_attributes() -> None:
    source = (
        '<html lang="zh-CN"><body><button title="更优" aria-label="更优">更优</button>'
        '<input placeholder="任务、意图、验收合同"></body></html>'
    )

    localized = localize_html(source, PurePosixPath("analysis/index.html"))

    assert 'title="Better"' in localized
    assert 'aria-label="Better"' in localized
    assert 'placeholder="Task, intent, or acceptance contract"' in localized


def test_reader_facing_ux_requires_home_core_tables_without_current_data(
    tmp_path: Path,
) -> None:
    pages = {
        tmp_path / "index.html": '<table id="home-core-metrics"></table>',
        tmp_path / "en/index.html": '<table id="home-core-metrics"></table>',
        tmp_path / "analysis/index.html": "普通读者可理解的说明",
    }

    _check_reader_facing_ux(tmp_path, pages)

    pages[tmp_path / "index.html"] = "<main>只有指标卡，没有核心比较表</main>"
    with pytest.raises(ValueError, match="core metrics table"):
        _check_reader_facing_ux(tmp_path, pages)


@pytest.mark.parametrize("term", FORBIDDEN_READER_TERMS)
def test_reader_facing_ux_rejects_unexplained_internal_terms(tmp_path: Path, term: str) -> None:
    pages = {
        tmp_path / "index.html": '<table id="home-core-metrics"></table>',
        tmp_path / "en/index.html": '<table id="home-core-metrics"></table>',
        tmp_path / "analysis/index.html": f"<p>{term}</p>",
    }

    with pytest.raises(ValueError, match="unexplained internal term"):
        _check_reader_facing_ux(tmp_path, pages)


def test_result_verification_is_readable_and_does_not_duplicate_raw_panels() -> None:
    result = {
        "evaluators_results": [
            {
                "evaluator_name": "AgentResponseEvaluator",
                "score": 0,
                "expected": {"status": "success", "retrieved_data": [1, 2]},
                "actual": {"status": "success", "retrieved_data": [1]},
                "assertions": [
                    {"assertion_msgs": ["Array is missing 1 expected element(s). Matched (1/2)"]}
                ],
            }
        ]
    }

    rendered = _verification_html(
        {"intent": "Return both records."},
        result,
        {"status": "success", "retrieved_data": [1]},
    )

    assert "最终数据缺少 1 项；官方期望 2 项，匹配 1 项" in rendered
    assert rendered.count("<h4>官方期望</h4>") == 1
    assert "<details open>" not in rendered
    assert "Ground truth 与实际最终回答" not in rendered


def test_paired_summary_uses_final_logical_results() -> None:
    rows = [
        {"engine": "chromium", "task_id": 1, "attempt": 1, "score": 1},
        {"engine": "moli", "task_id": 1, "attempt": 1, "score": 0},
        {"engine": "moli", "task_id": 1, "attempt": 2, "score": 1},
        {"engine": "chromium", "task_id": 2, "attempt": 1, "score": 0},
        {"engine": "moli", "task_id": 2, "attempt": 1, "score": 1},
    ]

    summary = _paired_summary(_logical_results(rows))["gpt-5.6-sol"]["comparisons"]["moli"]

    assert summary["paired"] == 2
    assert summary["both_pass"] == 1
    assert summary["comparison_only"] == 1
    assert summary["mcnemar_exact_p"] == 1.0


def test_comparison_components_scale_to_three_browsers(monkeypatch) -> None:
    monkeypatch.setattr(
        report_module,
        "ENGINE_LABELS",
        {"chromium": "Chromium", "moli": "Moli", "third": "Third Browser"},
    )
    monkeypatch.setattr(report_module, "BASELINE_ENGINE", "chromium")
    tasks = [
        {
            "task_id": 1,
            "rationale": "One task",
            "tier": "easy",
            "sites": ["gitlab"],
            "task_type": "NAVIGATE",
        }
    ]
    attempts = [
        {
            "engine": engine,
            "task_id": 1,
            "score": score,
            "browser_tool_duration_by_action_ms": {"click": duration},
            "browser_tool_count_by_action": {"click": count},
        }
        for engine, score, duration, count in (
            ("chromium", 1, 100, 2),
            ("moli", 0, 180, 2),
            ("third", 1, 120, 3),
        )
    ]

    comparison = _same_task_comparison(tasks, attempts)
    actions = _action_duration_cards(attempts)

    assert 'id="configuration-matrix"' in comparison
    assert comparison.count('class="outcome-pill pass"') == 2
    assert comparison.count('class="outcome-pill fail"') == 1
    assert "同题全配置矩阵" in comparison
    assert "Third Browser" in comparison
    assert "Third Browser" in actions
    assert "0.040 秒" in actions


def test_logical_results_obey_legacy_frozen_score_authority() -> None:
    rows = [
        {"engine": "chromium", "task_id": 1, "attempt": 1, "score": 0},
        {"engine": "chromium", "task_id": 1, "attempt": 2, "score": 1},
    ]

    result = _logical_results(rows, {"official_score_uses": "primary attempt only"})[0]

    assert result["score"] == 0


def test_logical_results_aggregate_browser_tool_wall_time(tmp_path: Path) -> None:
    event_paths = []
    for index, seconds in enumerate((0.4, 0.6), start=1):
        path = tmp_path / f"events-{index}.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "type": "tool_execution_start",
                        "data": {
                            "tool_call_id": str(index),
                            "tool_name": "browser",
                            "arguments": {"action": "click", "ref": index},
                        },
                    },
                    {
                        "type": "tool_execution_end",
                        "data": {
                            "tool_call_id": str(index),
                            "status": "success",
                            "execution_time": seconds,
                        },
                    },
                ]
            ),
            encoding="utf-8",
        )
        event_paths.append(path)
    rows = [
        {
            "engine": "chromium",
            "task_id": 1,
            "attempt": index,
            "score": 0,
            "paths": {"events": str(path)},
        }
        for index, path in enumerate(event_paths, start=1)
    ]

    result = _logical_results(rows)[0]

    assert result["action_count"] == 2
    assert result["browser_tool_duration_ms"] == 1000
    assert result["browser_tool_count_by_action"] == {"click": 2}


def test_logical_results_weight_mean_memory_by_resource_samples() -> None:
    rows = [
        {
            "engine": "chromium",
            "task_id": 1,
            "attempt": 1,
            "score": 0,
            "duration_ms": 1000,
            "resource": {"memory_mean_bytes": 100, "sample_count": 1},
        },
        {
            "engine": "chromium",
            "task_id": 1,
            "attempt": 2,
            "score": 1,
            "duration_ms": 1000,
            "resource": {"memory_mean_bytes": 300, "sample_count": 3},
        },
    ]

    result = _logical_results(rows)[0]

    assert result["resource"]["memory_mean_bytes"] == 250


def _use_single_model_profile(monkeypatch) -> None:
    real_read_json = report_module._read_json

    def read_json(path: Path) -> dict:
        value = real_read_json(path)
        if path == Path("evaluation/browser-use/current-report.json"):
            return {
                **value,
                "models": ["gpt-5.6-sol"],
                "model_labels": {"gpt-5.6-sol": "GPT-5.6 Sol"},
            }
        return value

    monkeypatch.setattr(report_module, "_read_json", read_json)
    monkeypatch.setattr(
        report_module,
        "load_failure_cause_review",
        lambda: {
            "schema": "lex_browser_use_layered_failure_cause_review/2",
            "cohorts": {},
        },
    )


def test_report_publishes_one_final_logical_result_and_aggregates_cost(
    tmp_path: Path, monkeypatch
) -> None:
    _use_single_model_profile(monkeypatch)
    evidence = tmp_path / "evidence"
    output = tmp_path / "report"
    evidence.mkdir()
    (evidence / "manifest.json").write_text(
        json.dumps({"protocol": {"model": "gpt-5.6-sol"}}), encoding="utf-8"
    )
    rows = [
        {
            "engine": "chromium",
            "task_id": 44,
            "tier": "easy",
            "attempt": 1,
            "score": 0,
            "duration_ms": 1000,
            "model_usage": {"total_tokens": 100},
        },
        {
            "engine": "chromium",
            "task_id": 44,
            "tier": "easy",
            "attempt": 2,
            "score": 1,
            "duration_ms": 900,
            "model_usage": {"total_tokens": 200},
        },
        {
            "engine": "moli",
            "task_id": 44,
            "tier": "easy",
            "attempt": 1,
            "score": 1,
            "duration_ms": 800,
            "model_usage": {"total_tokens": 80},
        },
    ]
    for row in rows:
        execution_dir = evidence / f"{row['engine']}-{row['attempt']}"
        execution_dir.mkdir()
        paths = {}
        for label, filename, payload in (
            ("response", "response.json", {"execution": row["attempt"]}),
            ("evaluation", "evaluation.json", {"score": row["score"]}),
            (
                "events",
                "events.json",
                [
                    {
                        "type": "tool_execution_start",
                        "data": {
                            "tool_call_id": str(row["attempt"]),
                            "tool_name": "browser",
                            "arguments": {"action": "click"},
                        },
                    },
                    {
                        "type": "tool_execution_end",
                        "data": {
                            "tool_call_id": str(row["attempt"]),
                            "status": "success",
                            "execution_time": row["attempt"] / 10,
                        },
                    },
                ],
            ),
        ):
            path = execution_dir / filename
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths[label] = str(path)
        for label, filename in (("network_har", "network.har"), ("agent_trace", "trace.zip")):
            path = execution_dir / filename
            path.write_bytes(f"private-{row['attempt']}".encode())
            paths[label] = str(path)
        row["paths"] = paths
    (evidence / "attempts.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    report = build_report(
        evidence=evidence,
        output=output,
        selection_path=Path("evaluation/browser-use/webarena-verified-pilot.json"),
    )
    text = report.read_text(encoding="utf-8")
    assert "1/1" in text
    data = json.loads((output / "report-data.json").read_text(encoding="utf-8"))
    assert data["summary"]["gpt-5.6-sol"]["chromium"]["score"] == 1
    chromium_summary = data["summary"]["gpt-5.6-sol"]["chromium"]
    assert chromium_summary["mean_duration_ms"] == 1900
    assert chromium_summary["p50_duration_ms"] == 1900
    assert chromium_summary["p95_duration_ms"] == 1900
    assert chromium_summary["mean_tokens"] == 300
    task = (output / "datasets/44.html").read_text(encoding="utf-8")
    assert "Ground truth · 官方验收合同" in task
    assert "没有唯一参考点击路径" in task
    assert "查看本题运行结果" in task
    assert "最终结果对照" not in task
    dataset = (output / "datasets/index.html").read_text(encoding="utf-8")
    assert "可完成性证据" not in dataset
    assert "data-result=" not in dataset
    for expected in ("均值", "P50", "P95", "comparison-winner"):
        assert expected in text
    analysis = (output / "analysis/index.html").read_text(encoding="utf-8")
    for expected in (
        "BrowserTool 动作延迟",
        "action-card-grid",
        "同题全配置对比",
            "browser-comparison",
            'id="configuration-matrix"',
            "完整 90 题结果入口",
            "全部配置 · 单次调用墙钟时间",
    ):
        assert expected in analysis
    assert analysis.index("同题全配置对比") < analysis.index("BrowserTool 动作延迟")
    assert "中位数" not in analysis
    assert "失败结果定位" not in analysis
    assert 'id="pair-table"' not in analysis
    review = (output / "review/index.html").read_text(encoding="utf-8")
    assert "核验与修订记录" in review
    assert "核验轮次" in review
    assert "逐结果覆盖" not in review
    result_id = "gpt-5.6-sol-chromium-44"
    attempt = (output / f"results/{result_id}.html").read_text(encoding="utf-8")
    for expected in (
        "任务要求与验收结果",
        "CowAgent 自主执行轨迹",
        "运行、日志与证据",
        f'href="results/{result_id}.html#trajectory"',
        "runtime-strip",
        "任务与验收",
        "在页面中查看完整执行证据",
        "下载原始 JSON",
    ):
        assert expected in attempt
    assert "没有 evaluator 明细" in attempt
    assert "Ground truth 与实际最终回答" not in attempt
    assert "官方 evaluator 逐规则判定" not in attempt
    for execution in (1, 2):
        artifact = output / f"artifacts/{result_id}/executions/{execution}/execution-evidence.json"
        assert artifact.is_file()
        bundle = json.loads(artifact.read_text(encoding="utf-8"))
        assert set(("agent_response", "evaluation", "agent_events", "trajectory")) <= set(bundle)
    assert "包含 <strong>2</strong> 次物理执行" in attempt
    assert "物理执行 1" in attempt
    assert "物理执行 2 · 判分权威" in attempt
    assert "SHA-256" in attempt
    assert "_inline_evidence" not in attempt
    viewer = (output / "evidence-viewer.html").read_text(encoding="utf-8")
    assert 'id="evidence-viewer-content"' in viewer
    assert "execution-evidence[.]json" in viewer
    assert "fetch(new URL(source,document.baseURI)" in viewer
    assert (output / "en/evidence-viewer.html").is_file()
    public_results = [
        json.loads(line)
        for line in (output / "data/results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    evidence_index = json.loads((output / "data/evidence-index.json").read_text(encoding="utf-8"))
    bundles = _load_evidence_bundles(evidence)
    logical = _logical_results(bundles[0]["source_attempts"])
    _check_execution_artifacts(output, logical, public_results, evidence_index)
    _check_public_privacy(output)
    legitimate_route = output / "legitimate-route.json"
    legitimate_route.write_text('{"href":"/users/sign_out"}', encoding="utf-8")
    _check_public_privacy(output)
    legitimate_route.unlink()
    index_row = next(item for item in evidence_index["results"] if item["result_id"] == result_id)
    assert index_row["execution_count"] == 2
    assert index_row["authoritative_execution"] == 2
    assert [item["duration_ms"] for item in index_row["executions"]] == [1000, 900]
    assert [item["model_usage"]["total_tokens"] for item in index_row["executions"]] == [
        100,
        200,
    ]
    assert [item["browser_tool_duration_ms"] for item in index_row["executions"]] == [
        100,
        200,
    ]
    assert all(
        artifact["sha256"]
        for execution in index_row["executions"]
        for artifact in execution["artifacts"].values()
    )
    assert all("_inline_evidence" not in execution for execution in index_row["executions"])
    private_trace = output / f"artifacts/{result_id}/executions/1/agent_trace.zip"
    private_trace.write_bytes(b"private trace")
    with pytest.raises(ValueError, match="private HAR or trace"):
        _check_public_privacy(output)
    with pytest.raises(ValueError, match="artifact tree differs"):
        _check_execution_artifacts(output, logical, public_results, evidence_index)
    private_trace.unlink()
    missing_response = output / f"artifacts/{result_id}/executions/1/execution-evidence.json"
    missing_response.unlink()
    with pytest.raises(ValueError, match="missing physical execution artifact"):
        _check_execution_artifacts(output, logical, public_results, evidence_index)
    assert "诊断重试" not in "".join(
        path.read_text(encoding="utf-8") for path in output.rglob("*.html")
    )
    formal_html = "".join(path.read_text(encoding="utf-8") for path in output.rglob("*.html"))
    for speculative_label in ("证据支持的诊断", "共享链路诊断", "失败归因："):
        assert speculative_label not in formal_html
    assert not list(output.rglob("*.svg"))


def test_common_success_resources_use_final_logical_results(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(report_module, "MODEL_LABELS", {"a": "A", "b": "B"})
    monkeypatch.setattr(
        report_module, "ENGINE_LABELS", {"chromium": "Chromium", "moli": "Moli"}
    )

    def execution(task_id: int, model: str, engine: str, attempt: int, score: int) -> dict:
        events = tmp_path / f"{task_id}-{model}-{engine}-{attempt}.json"
        events.write_text("[]", encoding="utf-8")
        return {
            "task_id": task_id,
            "model": model,
            "engine": engine,
            "attempt": attempt,
            "score": score,
            "duration_ms": 1000 * task_id + attempt,
            "model_usage": {"total_tokens": 100 * task_id + attempt},
            "resource": {"memory_peak_bytes": 1048576 * task_id, "cpu_ms": 10 * task_id},
            "paths": {"events": str(events)},
        }

    logical = []
    for task_id in (1, 2, 3):
        for model in ("a", "b"):
            for engine in ("chromium", "moli"):
                first_score = 1
                if task_id == 2 and model == "a" and engine == "moli":
                    first_score = 0
                if task_id == 3 and model == "b" and engine == "chromium":
                    first_score = 0
                sources = [execution(task_id, model, engine, 1, first_score)]
                final_score = first_score
                if task_id == 2 and first_score == 0:
                    sources.append(execution(task_id, model, engine, 2, 1))
                    final_score = 1
                logical.append(
                    {
                        **sources[-1],
                        "score": final_score,
                        "duration_ms": sum(item["duration_ms"] for item in sources),
                        "_source_executions": sources,
                    }
                )

    count, rows = _common_final_success_resource_rows(logical)

    assert count == 2
    assert {row[2] for row in rows} == {"1.5 秒", "2.5 秒"}
    assert {row[5] for row in rows} == {"1.5 MiB"}


def test_summary_winners_are_scoped_to_all_configurations(monkeypatch) -> None:
    monkeypatch.setattr(report_module, "MODEL_LABELS", {"a": "A", "b": "B"})
    monkeypatch.setattr(
        report_module, "ENGINE_LABELS", {"chromium": "Chromium", "moli": "Moli"}
    )

    def metrics(passed: int) -> dict:
        return {
            "completed": 10,
            "passed": passed,
            "score": passed / 10,
            **{
                name: float(passed * 1000 if "duration_ms" in name else passed)
                for name in (
                    "mean_duration_ms",
                    "p50_duration_ms",
                    "p95_duration_ms",
                    "mean_browser_tool_duration_ms",
                    "p50_browser_tool_duration_ms",
                    "p95_browser_tool_duration_ms",
                    "mean_tokens",
                    "p50_tokens",
                    "p95_tokens",
                )
            },
        }

    rows = _summary_comparison_rows(
        {
            "a": {"chromium": metrics(8), "moli": metrics(7)},
            "b": {"chromium": metrics(3), "moli": metrics(4)},
        }
    )

    assert "comparison-winner" in str(rows[0][2])
    assert "comparison-winner" not in str(rows[1][2])
    assert "comparison-winner" not in str(rows[2][2])
    assert "comparison-winner" not in str(rows[3][2])
    for metric_column in (4, 5, 6):
        assert "comparison-winner" in str(rows[2][metric_column])
        assert "comparison-winner" not in str(rows[3][metric_column])


def test_unknown_failure_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown failure-cause scope"):
        _failure_scope_counts(
            {"result": {"subcategory": {"code": "new_unregistered_scope"}}}
        )


def test_unknown_failed_evaluator_combination_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown failed evaluator combination"):
        _automatic_failure_type(
            {
                "evaluators_results": [
                    {"evaluator_name": "NewEvaluator", "score": 0}
                ]
            }
        )


def test_report_renders_measured_runtime_identities(tmp_path: Path, monkeypatch) -> None:
    _use_single_model_profile(monkeypatch)
    evidence = tmp_path / "evidence"
    output = tmp_path / "report"
    evidence.mkdir()
    (evidence / "run-config.json").write_text(
        json.dumps({"docker_context": "isolated-eval", "selection_sha256": "selection-hash"}),
        encoding="utf-8",
    )
    (evidence / "manifest.json").write_text(
        json.dumps(
            {
                "protocol": {
                    "cowagent_commit": "abc123",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "low",
                },
                "environment": {
                    "site_access_host": "192.168.5.2",
                    "docker_architecture": "aarch64",
                    "docker_server_version": "28.0",
                    "webarena_verified_version": "1.2.3",
                    "official_evaluator": {
                        "evaluator_checksum": "eval-checksum",
                    },
                    "browser_versions": {
                        "chromium": {"Browser": "Chrome/151"},
                        "moli": {"Browser": "Moli/1.1.3"},
                    },
                    "browser_images": {
                        "chromium": {"architecture": "arm64", "id": "sha256:chrome"},
                        "moli": {"architecture": "arm64", "id": "sha256:moli"},
                    },
                    "site_images": {
                        "reddit": {
                            "reference": "official/reddit:latest",
                            "repo_digests": ["official/reddit@sha256:site"],
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_report(
        evidence=evidence,
        output=output,
        selection_path=Path("evaluation/browser-use/webarena-verified-pilot.json"),
    )
    text = (output / "reproduce/index.html").read_text(encoding="utf-8")

    for expected in (
        "abc123",
        "GPT-5.6 Sol · reasoning=low",
        "eval-checksum",
        "selection-hash",
        "Chrome/151",
        "Moli/1.1.3",
    ):
        assert expected in text

    assert report.is_file()
    assert (output / "assets/webarena-autonomous-evaluation-flow.png").is_file()
    home_text = report.read_text(encoding="utf-8")
    assert "90 题 Browser Use 完整结果" in home_text
    assert "90 题核心指标" in home_text
    assert "首次成功与重试恢复" not in home_text
    methodology_text = (output / "methodology/index.html").read_text(encoding="utf-8")
    assert "webarena-autonomous-evaluation-flow.png" in methodology_text
    assert "ContainerManager.start(reset=True)" in methodology_text
    assert "192.168.5.2" in methodology_text
    assert "不含点击序列" in methodology_text
    for name in (
        "changes/index.html",
        "datasets/index.html",
        "methodology/index.html",
        "analysis/index.html",
        "review/index.html",
        "adoption/index.html",
        "reproduce/index.html",
        "accounts/index.html",
        "en/index.html",
        "engines/chromium.html",
        "engines/moli.html",
    ):
        page = output / name
        assert page.is_file()
        if name != "en/index.html":
            assert "切换主题" in page.read_text(encoding="utf-8")
    expected_tasks = len(load_pilot("evaluation/browser-use/webarena-verified-pilot.json").tasks)
    assert len(list((output / "datasets").glob("[0-9]*.html"))) == expected_tasks
    assert not (output / "guide").exists()
    report_data = json.loads((output / "report-data.json").read_text(encoding="utf-8"))
    assert "evidence" not in report_data
    evidence_index = json.loads((output / "data/evidence-index.json").read_text(encoding="utf-8"))
    assert evidence_index["schema"] == "lex_browser_use_public_evidence_index/4"
    assert len(evidence_index["results"]) == 0


def test_failure_diagnosis_surfaces_exact_evaluator_assertion() -> None:
    result = {
        "evaluators_results": [
            {
                "evaluator_name": "AgentResponseEvaluator",
                "score": 0,
                "assertions": [
                    {"assertion_msgs": ["Array is missing 1 expected element(s). Matched (1/2)"]}
                ],
            }
        ]
    }

    diagnosis = _failure_diagnosis(result, {})

    assert "规则 1 AgentResponseEvaluator" in diagnosis
    assert "Array is missing 1 expected element(s)" in diagnosis


def _write_model_evidence(path: Path, model: str, rows: list[dict]) -> None:
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps({"protocol": {"model": model}}), encoding="utf-8"
    )
    (path / "attempts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_two_models_and_two_browsers_keep_distinct_logical_results(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(report_module, "MODEL_LABELS", {"model-a": "A", "model-b": "B"})
    monkeypatch.setattr(report_module, "ENGINE_LABELS", {"chromium": "Chromium", "moli": "Moli"})
    model_a, model_b = tmp_path / "a", tmp_path / "b"
    rows = [
        {"engine": engine, "task_id": 44, "tier": "easy", "attempt": 1, "score": 1}
        for engine in ("chromium", "moli")
    ]
    _write_model_evidence(model_a, "model-a", rows)
    _write_model_evidence(model_b, "model-b", rows)

    bundles = _load_evidence_bundles([model_a, model_b])
    logical = [
        result for bundle in bundles for result in _logical_results(bundle["source_attempts"])
    ]

    assert len(logical) == 4
    assert len({report_module._attempt_id(row) for row in logical}) == 4
    assert set(report_module._primary_summary(logical)) == {"model-a", "model-b"}


def test_joint_report_builds_two_model_by_two_browser_matrix(tmp_path: Path, monkeypatch) -> None:
    model_a, model_b = tmp_path / "a", tmp_path / "b"
    rows = [
        {
            "engine": engine,
            "task_id": 44,
            "tier": "easy",
            "attempt": 1,
            "score": score,
        }
        for engine, score in (("chromium", 1), ("moli", 0))
    ]
    _write_model_evidence(model_a, "model-a", rows)
    _write_model_evidence(model_b, "model-b", rows)
    real_read_json = report_module._read_json

    def read_json(path: Path) -> dict:
        if path == Path("evaluation/browser-use/current-report.json"):
            return {
                "engines": ["chromium", "moli"],
                "engine_labels": {"chromium": "Chromium", "moli": "Moli"},
                "baseline_engine": "chromium",
                "models": ["model-a", "model-b"],
                "model_labels": {"model-a": "Model A", "model-b": "Model B"},
            }
        return real_read_json(path)

    monkeypatch.setattr(report_module, "_read_json", read_json)
    monkeypatch.setattr(
        report_module,
        "load_failure_cause_review",
        lambda: {
            "schema": "lex_browser_use_layered_failure_cause_review/2",
            "cohorts": {},
        },
    )
    output = tmp_path / "report"

    build_report(
        evidence=[model_a, model_b],
        output=output,
        selection_path=Path("evaluation/browser-use/webarena-verified-pilot.json"),
    )

    data = json.loads((output / "report-data.json").read_text(encoding="utf-8"))
    assert set(data["summary"]) == {"model-a", "model-b"}
    assert data["summary"]["model-a"]["chromium"]["completed"] == 1
    assert data["summary"]["model-b"]["moli"]["completed"] == 1
    assert len(list((output / "results").glob("*.html"))) == 4
    assert (output / "results/model-a-chromium-44.html").is_file()
    assert (output / "results/model-b-chromium-44.html").is_file()
    analysis = (output / "analysis/index.html").read_text(encoding="utf-8")
    assert 'data-filter="model"' in analysis
    assert 'data-model="model-a"' in analysis
    assert 'data-model="model-b"' in analysis


def test_multi_model_evidence_fails_closed_when_cohort_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(report_module, "MODEL_LABELS", {"model-a": "A", "model-b": "B"})
    model_a, model_b = tmp_path / "a", tmp_path / "b"
    _write_model_evidence(
        model_a,
        "model-a",
        [
            {"engine": engine, "task_id": 44, "attempt": 1, "score": 1}
            for engine in ("chromium", "moli")
        ],
    )
    _write_model_evidence(
        model_b,
        "model-b",
        [{"engine": "chromium", "task_id": 44, "attempt": 1, "score": 1}],
    )

    with pytest.raises(ValueError, match="model cohort differs"):
        _load_evidence_bundles([model_a, model_b])


def test_pairing_uses_dynamic_baseline_for_three_browsers(monkeypatch) -> None:
    monkeypatch.setattr(report_module, "MODEL_LABELS", {"model-a": "A"})
    monkeypatch.setattr(
        report_module,
        "ENGINE_LABELS",
        {"first": "First", "baseline": "Baseline", "third": "Third"},
    )
    monkeypatch.setattr(report_module, "BASELINE_ENGINE", "baseline")
    rows = [
        {
            "model": "model-a",
            "engine": engine,
            "task_id": task_id,
            "attempt": 1,
            "score": score,
        }
        for task_id, outcomes in ((1, (1, 0, 1)), (2, (0, 0, 1)))
        for engine, score in zip(("first", "baseline", "third"), outcomes, strict=True)
    ]

    comparisons = _paired_summary(_logical_results(rows))["model-a"]["comparisons"]

    assert set(comparisons) == {"first", "third"}
    assert comparisons["first"]["baseline_engine"] == "baseline"
    assert comparisons["first"]["comparison_only"] == 1
    assert comparisons["third"]["comparison_only"] == 2
