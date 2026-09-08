import json
from pathlib import Path

from harness.browser_agent.report import _paired_summary, build_report


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


def test_report_renders_measured_runtime_identities(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    output = tmp_path / "report"
    evidence.mkdir()
    (evidence / "run-config.json").write_text(
        json.dumps({"docker_context": "isolated-eval"}), encoding="utf-8"
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
                        "data_checksum": "data-checksum",
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
    text = report.read_text(encoding="utf-8")

    for expected in (
        "abc123",
        "gpt-6-astra · reasoning=low",
        "eval-checksum",
        "data-checksum",
        "Chrome/151",
        "Moli/1.1.3",
        "official/reddit@sha256:site",
    ):
        assert expected in text
