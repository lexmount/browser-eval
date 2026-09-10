import json
from pathlib import Path

from harness.browser_agent.report import (
    _failure_diagnosis,
    _logical_results,
    _paired_summary,
    build_report,
)


def test_paired_summary_uses_final_logical_results() -> None:
    rows = [
        {"engine": "chromium", "task_id": 1, "attempt": 1, "score": 1},
        {"engine": "moli", "task_id": 1, "attempt": 1, "score": 0},
        {"engine": "moli", "task_id": 1, "attempt": 2, "score": 1},
        {"engine": "chromium", "task_id": 2, "attempt": 1, "score": 0},
        {"engine": "moli", "task_id": 2, "attempt": 1, "score": 1},
    ]

    summary = _paired_summary(_logical_results(rows))

    assert summary["paired"] == 2
    assert summary["both_pass"] == 1
    assert summary["moli_only"] == 1
    assert summary["mcnemar_exact_p"] == 1.0


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


def test_report_publishes_one_final_logical_result_and_aggregates_cost(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    output = tmp_path / "report"
    evidence.mkdir()
    rows = [
        {
            "engine": "chromium",
            "task_id": 44,
            "tier": "easy",
            "attempt": 1,
            "score": 0,
            "duration_ms": 1000,
        },
        {
            "engine": "chromium",
            "task_id": 44,
            "tier": "easy",
            "attempt": 2,
            "score": 1,
            "duration_ms": 900,
        },
        {
            "engine": "moli",
            "task_id": 44,
            "tier": "easy",
            "attempt": 1,
            "score": 1,
            "duration_ms": 800,
        },
    ]
    (evidence / "attempts.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    report = build_report(
        evidence=evidence,
        output=output,
        selection_path=Path("evaluation/browser-agent/webarena-verified-pilot.json"),
    )
    text = report.read_text(encoding="utf-8")
    assert "1/1" in text
    data = json.loads((output / "report-data.json").read_text(encoding="utf-8"))
    assert data["summary"]["chromium"]["score"] == 1
    chromium_summary = data["summary"]["chromium"]
    assert chromium_summary["mean_duration_ms"] == 1900
    assert chromium_summary["p50_duration_ms"] == 1900
    assert chromium_summary["p95_duration_ms"] == 1900
    task = (output / "datasets/44.html").read_text(encoding="utf-8")
    assert "Ground truth · 官方验收合同" in task
    assert "没有唯一参考点击路径" in task
    assert "查看本题运行结果" in task
    assert "最终结果对照" not in task
    dataset = (output / "datasets/index.html").read_text(encoding="utf-8")
    assert "可完成性证据" not in dataset
    assert 'data-result=' not in dataset
    analysis = (output / "analysis/index.html").read_text(encoding="utf-8")
    for expected in ("均值", "P50", "P95", "comparison-winner"):
        assert expected in analysis
    assert "中位数" not in analysis
    assert "失败结果定位" not in analysis
    review = (output / "review/index.html").read_text(encoding="utf-8")
    assert "核验与修订记录" in review
    assert "核验轮次" in review
    assert "逐结果覆盖" not in review
    attempt = (output / "results/chromium-44.html").read_text(encoding="utf-8")
    for expected in (
        "Agent 实际回答",
        "官方 evaluator 逐规则判定",
        "CowAgent 自主执行轨迹",
        "运行、日志与证据",
        'href="results/chromium-44.html#trajectory"',
    ):
        assert expected in attempt
    for artifact in (
        "agent-response.json",
        "evaluation.json",
        "agent-events.json",
        "trajectory.json",
        "evidence-metadata.json",
    ):
        assert (output / "artifacts/chromium-44" / artifact).is_file()
    assert "诊断重试" not in "".join(
        path.read_text(encoding="utf-8") for path in output.rglob("*.html")
    )


def test_report_renders_measured_runtime_identities(tmp_path: Path) -> None:
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
                    "model": "gpt-6-astra",
                    "reasoning_effort": "low",
                },
                "environment": {
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
        selection_path=Path("evaluation/browser-agent/webarena-verified-pilot.json"),
    )
    text = (output / "reproduce/index.html").read_text(encoding="utf-8")

    for expected in (
        "abc123",
        "gpt-6-astra · reasoning=low",
        "eval-checksum",
        "selection-hash",
        "Chrome/151",
        "Moli/1.1.3",
    ):
        assert expected in text

    assert report.is_file()
    assert (output / "assets/webarena-autonomous-evaluation-flow.png").is_file()
    home_text = report.read_text(encoding="utf-8")
    assert "Chromium 与 Moli：Browser Use 能力实测" in home_text
    methodology_text = (output / "methodology/index.html").read_text(encoding="utf-8")
    assert "webarena-autonomous-evaluation-flow.png" in methodology_text
    assert "ContainerManager.start(reset=True)" in methodology_text
    assert "192.168.5.2" in methodology_text
    assert "不含点击序列" in methodology_text
    for name in (
        "guide/index.html",
        "guide/article.html",
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
        if name not in {"guide/article.html", "en/index.html"}:
            assert "切换主题" in page.read_text(encoding="utf-8")
    assert len(list((output / "datasets").glob("[0-9]*.html"))) == 30
    assert (output / "guide/article.html").read_text(encoding="utf-8").find("<script") == -1
    report_data = json.loads((output / "report-data.json").read_text(encoding="utf-8"))
    assert "evidence" not in report_data
    evidence_index = json.loads((output / "data/evidence-index.json").read_text(encoding="utf-8"))
    assert evidence_index["schema"] == "lex_browser_agent_public_evidence_index/2"
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
