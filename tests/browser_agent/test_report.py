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
