import json
from pathlib import Path

from harness.browser_agent.report import _failure_diagnosis, _paired_summary, build_report


def test_paired_summary_uses_primary_attempts_only() -> None:
    rows = [
        {"engine": "chromium", "task_id": 1, "attempt": 1, "score": 1},
        {"engine": "moli", "task_id": 1, "attempt": 1, "score": 0},
        {"engine": "moli", "task_id": 1, "attempt": 2, "score": 1},
        {"engine": "chromium", "task_id": 2, "attempt": 1, "score": 0},
        {"engine": "moli", "task_id": 2, "attempt": 1, "score": 1},
    ]

    summary = _paired_summary(rows)

    assert summary["paired"] == 2
    assert summary["chromium_only"] == 1
    assert summary["moli_only"] == 1
    assert summary["mcnemar_exact_p"] == 1.0


def test_report_keeps_primary_score_and_retry_recovery_separate(tmp_path: Path) -> None:
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
    assert "0/1" in text
    assert "1/1" in text
    data = json.loads((output / "report-data.json").read_text(encoding="utf-8"))
    assert data["summary"]["chromium"]["score"] == 0
    assert data["summary"]["chromium"]["recovered"] == 1
    task = (output / "datasets/44.html").read_text(encoding="utf-8")
    assert "Ground truth · 官方验收合同" in task
    assert "没有唯一参考点击路径" in task
    assert "参考状态 · 当前环境是否有成功路径证据" in task
    assert "全部试次结果对照" in task
    assert "本轮已有成功轨迹" in (output / "datasets/index.html").read_text(encoding="utf-8")
    attempt = (output / "attempts/chromium-44-a1.html").read_text(encoding="utf-8")
    for expected in (
        "Agent 实际回答",
        "官方 evaluator 逐规则判定",
        "CowAgent 自主执行轨迹",
        "运行、日志与证据",
        'href="attempts/chromium-44-a1.html#trajectory"',
    ):
        assert expected in attempt
    for artifact in (
        "agent-response.json",
        "evaluation.json",
        "agent-events.json",
        "trajectory.json",
        "evidence-metadata.json",
    ):
        assert (output / "artifacts/chromium-44-a1" / artifact).is_file()


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
    assert "ContainerManager.start()" in methodology_text
    assert "不是固定动作回放" in methodology_text
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
    assert evidence_index["schema"] == "lex_browser_agent_public_evidence_index/1"


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
