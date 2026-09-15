import hashlib
import json
from pathlib import Path

from scripts.project_headless_providers import project_bundle
from harness.web_fetch.result_review import require_result_review


def test_projection_keeps_reviewed_attempts_and_rebinds_receipts(tmp_path: Path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    dataset = {
        "coverage_plan": {"web": ["keep", "remove"]},
        "comparison": {"require_independent_result_review": True, "conversion": "old"},
        "cases": [{"id": "a", "task_kind": "body"}],
    }
    records = [
        {"attempt_id": f"{provider}__a", "provider": provider, "case_id": "a",
         "artifact_path": f"artifacts/{provider}.txt", "ok": True,
         "metrics": {"content_pass": True, "content_sha256": provider * 8}}
        for provider in ("keep", "remove")
    ]
    (source / "dataset.json").write_text(json.dumps(dataset))
    (source / "results.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )
    review = {
        "schema_version": 1,
        "review_protocol": {"scope": "all", "machine_judgments_withheld": True},
        "bindings": {
            "dataset_sha256": hashlib.sha256((source / "dataset.json").read_bytes()).hexdigest(),
            "results_sha256": hashlib.sha256((source / "results.jsonl").read_bytes()).hexdigest(),
        },
        "attempts": [
            {"attempt_id": record["attempt_id"], "machine_verdict": "pass",
             "agent_verdict": "pass", "final_verdict": "pass"}
            for record in records
        ],
    }
    (source / "result-review.json").write_text(json.dumps(review))
    (source / "latency-corrections.json").write_text(json.dumps({
        "selection": "historical Moli three-version correction",
        "changes": [{"provider": provider, "case_id": "a"}
                    for provider in ("keep", "remove")],
        "diagnostic_summary": {provider: {"calls": 3}
                               for provider in ("keep", "remove")},
    }))

    project_bundle(source, output, ["keep"], conversion="current")

    projected_dataset = json.loads((output / "dataset.json").read_text())
    projected_records = [json.loads(line) for line in
                         (output / "results.jsonl").read_text().splitlines()]
    assert projected_dataset["coverage_plan"] == {"web": ["keep"]}
    assert projected_dataset["comparison"]["conversion"] == "current"
    assert [record["provider"] for record in projected_records] == ["keep"]
    assert require_result_review(output, projected_dataset, projected_records)["reviewed_attempts"] == 1
    projected_review = json.loads((output / "result-review.json").read_text())
    assert projected_review["projection"]["removed_providers"] == ["remove"]
    projected_latency = json.loads((output / "latency-corrections.json").read_text())
    assert projected_latency["diagnostic_summary"] == {"keep": {"calls": 3}}
    assert projected_latency["superseded_logical_results"] == 1
    assert projected_latency["projection"]["removed_providers"] == ["remove"]
    assert "Moli" not in projected_latency["selection"]
    scoring = json.loads((output / "scoring-revisions.json").read_text())[-1]
    assert "remove" in scoring["projection"]
    assert "Moli" not in scoring["projection"]
