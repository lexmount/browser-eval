"""Offline revisions must reject invalid prior authority without changing evidence."""

import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.reference_lifecycle import (
    HOMEPAGE_READER_SCOPE_PROMPTS,
    valid_scoring_revision,
)
from harness.web_fetch.scoring import SCORING_SOURCE_SHA256, score_attempt
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


def test_reader_prompt_can_follow_scoring_contract_without_changing_saved_call(tmp_path: Path):
    evidence, canonical, original_case = revision_fixture(tmp_path, b"[]")
    original_record = json.loads((evidence / "results.jsonl").read_text())
    measured = json.loads((evidence / "dataset.json").read_text())
    measured_case = measured["cases"][0]
    measured_case["source_benchmark"] = {"mode": "ranked_domain_homepage"}
    measured_case["task_prompt"] = "Return this exact answer: expected answer"
    measured_case["acceptance_contract"] = {
        "version": 1,
        "schema": "body_text",
        "minimum_records": 0,
        "stable_text_anchors": [{"id": "body", "alternatives": ["expected answer"]}],
    }
    (evidence / "dataset.json").write_text(json.dumps(measured))
    candidate = json.loads(canonical.read_text())
    candidate["cases"][0].update(measured_case)
    candidate["cases"][0]["required_patterns"] = [
        {"id": "fact", "pattern": "missing fact"}
    ]
    candidate["cases"][0]["acceptance_contract"]["stable_text_anchors"] = [
        {"id": "body", "alternatives": ["missing fact"]}
    ]
    candidate["cases"][0]["task_prompt"] = next(iter(HOMEPAGE_READER_SCOPE_PROMPTS))
    candidate["cases"][0]["reference_answer"] = (
        "Stable task-relevant body from the requested page."
    )
    canonical.write_text(json.dumps(candidate))

    receipt = revise(
        evidence,
        canonical,
        root_cause_category="answer-surface",
        root_cause="The reader-facing scope described obsolete answer tokens that providers never receive.",
        regression_ids=["answer-title-supplies-required-fact"],
    )

    assert receipt["provider_calls"] == 0
    assert receipt["changed_cases"] == [
        {"case_id": "one", "fields": [
            "acceptance_contract", "reference_answer", "required_patterns", "task_prompt",
        ]}
    ]
    current = json.loads((evidence / "results.jsonl").read_text())
    for key in ("attempt_id", "run_id", "elapsed_ms", "artifact_path"):
        assert current[key] == original_record[key]
    assert current["metrics"]["content_sha256"] == original_record["metrics"]["content_sha256"]


def test_reader_prompt_revision_cannot_change_provider_url(tmp_path: Path):
    evidence, canonical, _ = revision_fixture(tmp_path, b"[]")
    candidate = json.loads(canonical.read_text())
    candidate["cases"][0]["task_prompt"] = "Return the current stable page body."
    candidate["cases"][0]["url"] = "https://other.example.invalid/"
    canonical.write_text(json.dumps(candidate))
    before = {path.name: path.read_bytes() for path in evidence.iterdir()}

    with pytest.raises(ValueError, match="captured task differs from canonical url"):
        revise(
            evidence,
            canonical,
            root_cause_category="answer-surface",
            root_cause="The reader-facing scope changed but the physical provider URL must remain frozen.",
            regression_ids=["answer-title-supplies-required-fact"],
        )

    assert {path.name: path.read_bytes() for path in evidence.iterdir()} == before


def test_scorer_drift_and_task_rule_change_are_rejected_atomically(tmp_path: Path):
    evidence, canonical, _ = revision_fixture(tmp_path, b"[]")
    row = json.loads((evidence / "results.jsonl").read_text())
    row["metrics"]["scoring_source_sha256"] = "0" * 64
    (evidence / "results.jsonl").write_text(json.dumps(row) + "\n")
    before = {path.name: path.read_bytes() for path in evidence.iterdir()}

    with pytest.raises(ValueError, match="scorer-only revision first"):
        revise(
            evidence,
            canonical,
            root_cause_category="answer-surface",
            root_cause="A shared answer-surface correction changed the scorer implementation.",
            regression_ids=["answer-title-supplies-required-fact"],
        )

    assert {path.name: path.read_bytes() for path in evidence.iterdir()} == before


def test_scorer_drift_and_task_rule_change_can_be_recorded_in_two_steps(tmp_path: Path):
    evidence, canonical, _ = revision_fixture(tmp_path, b"[]")
    row = json.loads((evidence / "results.jsonl").read_text())
    prior_scoring_source = "0" * 64
    row["metrics"]["scoring_source_sha256"] = prior_scoring_source
    (evidence / "results.jsonl").write_text(json.dumps(row) + "\n")
    authority = {
        "root_cause_category": "answer-surface",
        "root_cause": "A shared answer-surface correction changed the scorer implementation.",
        "regression_ids": ["answer-title-supplies-required-fact"],
    }

    scorer_receipt = revise(evidence, evidence / "dataset.json", **authority)
    assert scorer_receipt["changed_cases"] == []
    assert scorer_receipt["input_scoring_source_sha256"] == prior_scoring_source
    assert scorer_receipt["output_scoring_source_sha256"] == SCORING_SOURCE_SHA256

    rule_receipt = revise(evidence, canonical, **authority)
    assert rule_receipt["input_scoring_source_sha256"] == SCORING_SOURCE_SHA256
    assert rule_receipt["output_scoring_source_sha256"] == SCORING_SOURCE_SHA256
    assert rule_receipt["changed_cases"] == [
        {"case_id": "one", "fields": ["required_patterns"]}
    ]
    assert valid_scoring_revision(evidence)
    history = json.loads((evidence / "scoring-revisions.json").read_text())
    history[-1]["output_scoring_source_sha256"] = "f" * 64
    (evidence / "scoring-revisions.json").write_text(json.dumps(history))
    assert not valid_scoring_revision(evidence)
    history[-1].pop("input_scoring_source_sha256")
    history[-1].pop("output_scoring_source_sha256")
    (evidence / "scoring-revisions.json").write_text(json.dumps(history))
    assert not valid_scoring_revision(evidence)


def test_scorer_only_revision_accepts_an_assembled_mixed_source_cohort(tmp_path: Path):
    evidence, _, case = revision_fixture(tmp_path, b"[]")
    dataset = json.loads((evidence / "dataset.json").read_text())
    second_case = {**case, "id": "two", "name": "Two"}
    dataset["cases"].append(second_case)
    (evidence / "dataset.json").write_text(json.dumps(dataset))
    rows = [json.loads((evidence / "results.jsonl").read_text())]
    rows[0]["metrics"]["scoring_source_sha256"] = "0" * 64
    second = {**rows[0], "attempt_id": "custom__two__r001", "case_id": "two",
              "case_name": "Two", "metrics": dict(rows[0]["metrics"])}
    second["metrics"]["scoring_source_sha256"] = "1" * 64
    rows.append(second)
    (evidence / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    (evidence / "manifest.json").write_text(json.dumps({
        "attempt_ids": [row["attempt_id"] for row in rows]
    }))

    receipt = revise(
        evidence,
        evidence / "dataset.json",
        root_cause_category="answer-surface",
        root_cause="An assembled cohort retains outputs scored by two older scorer builds.",
        regression_ids=["answer-title-supplies-required-fact"],
    )

    assert receipt["input_scoring_source_sha256s"] == ["0" * 64, "1" * 64]
    assert receipt["output_scoring_source_sha256"] == SCORING_SOURCE_SHA256
    assert valid_scoring_revision(evidence)


@pytest.mark.parametrize('failed', [False, True])
def test_scoring_validation_preserves_complete_saved_attempt_bytes(tmp_path, failed):
    evidence, _, case = revision_fixture(tmp_path, b'[]')
    row = json.loads((evidence / 'results.jsonl').read_text())
    if failed:
        raw = b'other content\n' * 20
        (evidence / 'answer.txt').write_bytes(raw)
        row['metrics'] = score_attempt(case, raw, ok=True)
    row['scoring'] = {'scored_at':'frozen-time', 'mode':'original-scoring'}
    row['diagnosis'] = {'retained':'original annotation'}
    row['failure_category'] = 'original category'
    (evidence / 'results.jsonl').write_text(json.dumps(row) + '\n')
    before = {name:(evidence / name).read_bytes()
              for name in ('dataset.json','results.jsonl','answer.txt','summary.json')}
    receipt = revise(evidence, evidence / 'dataset.json', validate_only=True,
        root_cause_category='answer-surface',
        root_cause='Validate the saved scoring without rewriting a physical-call record.',
        regression_ids=['answer-title-supplies-required-fact'])
    assert receipt['validation_only'] is True
    assert receipt['task_decision_changes'] == []
    assert receipt['input_results_sha256'] == receipt['output_results_sha256']
    assert valid_scoring_revision(evidence)
    assert {name:(evidence / name).read_bytes() for name in before} == before


@pytest.mark.parametrize('damage', ['score','requirements'])
def test_scoring_validation_rejects_changed_verdict_or_requirements_atomically(tmp_path, damage):
    evidence, canonical, _ = revision_fixture(tmp_path, b'[]')
    if damage == 'score':
        canonical = evidence / 'dataset.json'
        row = json.loads((evidence / 'results.jsonl').read_text())
        row['metrics']['content_pass'] = False
        (evidence / 'results.jsonl').write_text(json.dumps(row) + '\n')
    before = {p.name:p.read_bytes() for p in evidence.iterdir()}
    with pytest.raises(ValueError):
        revise(evidence, canonical, validate_only=True,
            root_cause_category='answer-surface',
            root_cause='Validation rejects changed scores and requirements without writing evidence.',
            regression_ids=['answer-title-supplies-required-fact'])
    assert {p.name:p.read_bytes() for p in evidence.iterdir()} == before


def test_report_assets_can_update_together_without_changing_scored_calls(tmp_path):
    from harness.web_fetch.scoring_revision import rebind_report_interpretation
    evidence, canonical, _ = revision_fixture(tmp_path, b'[]')
    revise(evidence, canonical, root_cause_category='answer-surface',
        root_cause='Synthetic required-answer correction has an executable acceptance control.',
        regression_ids=['answer-title-supplies-required-fact'])
    calls = (evidence / 'results.jsonl').read_bytes()
    content = evidence / 'report-assets' / 'browser-interpretation.json'
    content.parent.mkdir()
    content.write_text('{"title":"New report"}')
    implementation = evidence / 'implementation-provenance.json'
    implementation.write_text('{"implementations":[{"version":"source build"}]}')
    binding = lambda p: {'path':p.relative_to(evidence).as_posix(),
                         'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    receipt = rebind_report_interpretation(evidence, binding(content),
                                          implementation_binding=binding(implementation))
    assert receipt['bound_asset_fields'] == ['report_interpretation', 'implementation_provenance']
    assert valid_scoring_revision(evidence)
    assert (evidence / 'results.jsonl').read_bytes() == calls
    dataset = json.loads((evidence / 'dataset.json').read_text())
    dataset['cases'][0]['required_patterns'] = []
    raw = (json.dumps(dataset, ensure_ascii=False, indent=2) + '\n').encode()
    (evidence / 'dataset.json').write_bytes(raw)
    (evidence / receipt['output_dataset_path']).write_bytes(raw)
    receipt['output_dataset_sha256'] = hashlib.sha256(raw).hexdigest()
    (evidence / 'report-binding-revisions.json').write_text(json.dumps([receipt]))
    assert not valid_scoring_revision(evidence)


@pytest.mark.parametrize('damage', ['wrong-hash', 'wrong-path', 'missing', 'symlink'])
def test_report_asset_update_rejects_unbound_provenance_before_writing(tmp_path, damage):
    from harness.web_fetch.scoring_revision import rebind_report_interpretation
    evidence, canonical, _ = revision_fixture(tmp_path, b'[]')
    revise(evidence, canonical, root_cause_category='answer-surface',
        root_cause='Synthetic required-answer correction has an executable acceptance control.',
        regression_ids=['answer-title-supplies-required-fact'])
    content = evidence / 'article.json'
    content.write_text('{}')
    implementation = evidence / 'implementation-provenance.json'
    implementation.write_text('{}')
    candidate = {'path':implementation.name,
                 'sha256':hashlib.sha256(implementation.read_bytes()).hexdigest()}
    if damage == 'wrong-hash':
        candidate['sha256'] = '0' * 64
    elif damage == 'wrong-path':
        candidate['path'] = '../implementation-provenance.json'
    elif damage == 'symlink':
        target = evidence / 'other-provenance.json'
        target.write_bytes(implementation.read_bytes())
        implementation.unlink()
        implementation.symlink_to(target.name)
    else:
        implementation.unlink()
    before = {p.relative_to(evidence):p.read_bytes() for p in evidence.rglob('*') if p.is_file()}
    with pytest.raises((ValueError, OSError)):
        rebind_report_interpretation(evidence,
            {'path':content.name,'sha256':hashlib.sha256(content.read_bytes()).hexdigest()},
            implementation_binding=candidate)
    assert {p.relative_to(evidence):p.read_bytes() for p in evidence.rglob('*') if p.is_file()} == before
