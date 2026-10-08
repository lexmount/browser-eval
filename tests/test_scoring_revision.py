"""Offline revisions must reject invalid prior authority without changing evidence."""

import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.reference_lifecycle import valid_scoring_revision
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.scoring_revision import revise


def revision_fixture(tmp_path: Path, history: bytes):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    case = {
        "id": "one",
        "name": "One",
        "url": "https://example.invalid/",
        "category": "web",
        "track": "web",
        "task_kind": "body",
        "required_patterns": [{"id": "fact", "pattern": "expected answer"}],
    }
    raw = b"expected answer\n" * 20
    (evidence / "answer.txt").write_bytes(raw)
    (evidence / "dataset.json").write_text(json.dumps({"cases": [case]}))
    row = {
        "attempt_id": "custom__one__r001",
        "provider": "custom",
        "provider_type": "synthetic",
        "case_id": "one",
        "case_name": "One",
        "category": "web",
        "artifact_path": "answer.txt",
        "ok": True,
        "run_id": "frozen-call",
        "elapsed_ms": 10,
        "metrics": score_attempt(case, raw, ok=True),
    }
    assert row["metrics"]["content_sha256"] == hashlib.sha256(raw).hexdigest()
    (evidence / "results.jsonl").write_text(json.dumps(row) + "\n")
    (evidence / "manifest.json").write_text(json.dumps({"attempt_ids": [row["attempt_id"]]}))
    (evidence / "summary.json").write_text("{}")
    (evidence / "scoring-revisions.json").write_bytes(history)
    canonical = tmp_path / "canonical.json"
    canonical.write_text(
        json.dumps(
            {"cases": [{**case, "required_patterns": [{"id": "fact", "pattern": "missing fact"}]}]}
        )
    )
    return evidence, canonical, case


@pytest.mark.parametrize("history", [b"{invalid", b"{}", b"[{}]"])
def test_invalid_history_rejects_before_any_evidence_write(tmp_path: Path, history: bytes):
    evidence, canonical, _ = revision_fixture(tmp_path, history)
    before = {p.name: p.read_bytes() for p in evidence.iterdir()}
    with pytest.raises(ValueError):
        revise(
            evidence,
            canonical,
            root_cause_category="answer-surface",
            root_cause="Synthetic correction restores a required fact in the answer surface.",
            regression_ids=["answer-title-supplies-required-fact"],
        )
    assert {p.name: p.read_bytes() for p in evidence.iterdir()} == before


def test_valid_history_can_extend_and_retains_prior_receipts(tmp_path: Path):
    evidence, canonical, original_case = revision_fixture(tmp_path, b"[]")
    original_record = json.loads((evidence / "results.jsonl").read_bytes())
    assert original_record["metrics"]["content_pass"] is True
    authority = {
        "root_cause_category": "answer-surface",
        "root_cause": "Synthetic correction restores a required fact in the answer surface.",
        "regression_ids": ["answer-title-supplies-required-fact"],
    }
    first = revise(evidence, canonical, **authority)
    assert first["task_decision_changes"] == [
        {"attempt_id": original_record["attempt_id"], "before": True, "after": False}
    ]
    assert valid_scoring_revision(evidence)
    canonical.write_text(json.dumps({"cases": [original_case]}))
    second = revise(evidence, canonical, **authority)
    assert second["task_decision_changes"] == [
        {"attempt_id": original_record["attempt_id"], "before": False, "after": True}
    ]
    assert valid_scoring_revision(evidence)
    assert json.loads((evidence / "scoring-revisions.json").read_bytes()) == [first, second]
    current = json.loads((evidence / "results.jsonl").read_bytes())
    for key in ("attempt_id", "run_id", "elapsed_ms", "artifact_path"):
        assert current[key] == original_record[key]
    assert current["metrics"]["content_sha256"] == original_record["metrics"]["content_sha256"]
