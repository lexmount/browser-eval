"""Scenario checks for the full-cohort evidence boundary."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_certified_webmainbench_full import verify_shard
from scripts.assemble_webmainbench_full import public_value
from scripts.score_certified_webmainbench_full import verify_score
from scripts.score_webmainbench_results import aggregate


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _cohort(tmp_path: Path) -> Path:
    shard = tmp_path / "moli"
    artifact_dir = shard / "artifacts"
    artifact_dir.mkdir(parents=True)
    rows = []
    calls = []
    for case_id in ("one", "two"):
        body = f"answer for {case_id}".encode()
        logical_path = f"artifacts/{case_id}.md"
        physical_path = f"artifacts/{case_id}-p01.txt"
        (shard / logical_path).write_bytes(body)
        (shard / physical_path).write_bytes(body)
        rows.append({"provider": "moli", "track_id": case_id, "ok": True,
                     "artifact_path": logical_path, "output_sha256": _digest(body),
                     "physical_attempt_count": 1})
        calls.append({"attempt_id": f"moli-{case_id}-1", "track_id": case_id,
                      "physical_artifact_path": physical_path, "output_sha256": _digest(body)})
    _write_jsonl(shard / "results.jsonl", rows)
    _write_jsonl(shard / "physical-attempts.jsonl", calls)
    _write_json(shard / "dataset.json", {"case_count": 2, "case_ids": ["one", "two"]})
    _write_json(shard / "execution.json", {"case_count": 2, "provider": "moli",
                                             "source_dataset_sha256": "source"})
    return shard


def test_complete_cohort_has_bound_logical_and_physical_evidence(tmp_path):
    shard = _cohort(tmp_path)
    result = verify_shard(shard, "moli", {"one", "two"},
                          {"case_count": 2, "source_dataset_sha256": "source"})
    assert result["logical_results"] == result["physical_calls"] == 2


def test_missing_case_cannot_be_certified(tmp_path):
    shard = _cohort(tmp_path)
    rows = [json.loads(line) for line in (shard / "results.jsonl").read_text().splitlines()]
    _write_jsonl(shard / "results.jsonl", rows[:1])
    with pytest.raises(ValueError, match="incomplete or mismatched"):
        verify_shard(shard, "moli", {"one", "two"},
                     {"case_count": 2, "source_dataset_sha256": "source"})


def test_changed_physical_output_cannot_be_certified(tmp_path):
    shard = _cohort(tmp_path)
    (shard / "artifacts/one-p01.txt").write_text("changed")
    with pytest.raises(ValueError, match="physical artifact mismatch"):
        verify_shard(shard, "moli", {"one", "two"},
                     {"case_count": 2, "source_dataset_sha256": "source"})


def test_public_evidence_hides_local_build_path_without_losing_error():
    row = {"error_message": "panic at /Users/runner/.cargo/html5ever/mod.rs:254:9",
           "meta": ["/private/tmp/run-123/output.txt"]}
    public = public_value(row)
    assert "/Users/" not in json.dumps(public)
    assert "/private/tmp/" not in json.dumps(public)
    assert "html5ever/mod.rs:254:9" in public["error_message"]


def test_scored_cohort_keeps_exact_measured_results_and_scorer_source(tmp_path):
    shard = _cohort(tmp_path)
    raw = [json.loads(line) for line in (shard / "results.jsonl").read_text().splitlines()]
    names = ("text_edit", "code_edit", "formula_edit", "table_edit", "table_TEDS")
    scored = [{**row, "metrics": {name: {"score": .5, "success": True} for name in names}} for row in raw]
    _write_jsonl(shard / "scored-results.jsonl", scored)
    scorer = tmp_path / "scorer"
    scorer.mkdir()
    (scorer / "metric.py").write_text("frozen scorer")
    _write_json(shard / "score-summary.json", {
        "provider": "moli", "case_count": 2, "official_aggregate": aggregate(scored),
        "scorer": {"commit": "pinned", "metric_config": {"use_llm": False},
                   "metric_source_sha256": {"metric.py": _digest(b"frozen scorer")}},
    })
    assert verify_score(shard, "moli", {"one", "two"}, scorer, {
        "case_count": 2, "official_scorer_commit": "pinned", "metric_config": {"use_llm": False},
    })["logical_results"] == 2
    scored[0]["ok"] = False
    _write_jsonl(shard / "scored-results.jsonl", scored)
    with pytest.raises(ValueError, match="differs from measured"):
        verify_score(shard, "moli", {"one", "two"}, scorer, {
            "case_count": 2, "official_scorer_commit": "pinned", "metric_config": {"use_llm": False},
        })
