"""Validate an independently produced, attempt-complete result review."""
import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from harness.record_io import file_sha256, load_jsonl, parse_jsonl

from .anomaly_review import require_anomaly_review
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.paths import contained_path

ANSWER_METADATA_FIELDS = ('links', 'images', 'videos', 'media', 'extras')


def review_answer_metadata(record, *, include_resources=True):
    """Return source answer fields a blind reviewer may inspect, never scores."""
    metadata = record.get('provider_metadata', {})
    answer = ({key: metadata[key] for key in ANSWER_METADATA_FIELDS if key in metadata}
              if include_resources else {})
    if record.get('title') is not None:
        answer['title'] = record['title']
    return answer


def review_raw_artifacts(record):
    """Return every persisted raw/file artifact binding used during review."""
    metadata = record.get('provider_metadata', {})
    artifacts = []
    if metadata.get('raw_response_path'):
        artifacts.append({
            'kind': 'raw_response', 'path': metadata.get('raw_response_path'),
            'sha256': metadata.get('raw_response_sha256'),
            'bytes': metadata.get('raw_response_bytes'),
        })
    if metadata.get('raw_stdout_path'):
        artifacts.append({
            'kind': 'raw_stdout', 'path': metadata.get('raw_stdout_path'),
            'sha256': metadata.get('raw_stdout_sha256'),
            'bytes': metadata.get('raw_stdout_bytes'),
        })
    for item in metadata.get('file_evaluation', {}).get('files', []):
        if item.get('artifact_path'):
            artifacts.append({
                'kind': 'file', 'path': item.get('artifact_path'),
                'sha256': item.get('sha256'), 'bytes': item.get('bytes'),
                'mime': item.get('mime'), 'pass': item.get('pass'),
            })
    return artifacts


def _sha256(path):
    return file_sha256(path)


def review_input_identity(record):
    """Return every record field that can select evidence shown to a reviewer."""
    metadata = record.get('provider_metadata', {})
    raw_artifacts = review_raw_artifacts(record)
    return {
        'provider': record.get('provider'),
        'case_id': record.get('case_id'),
        'artifact_path': record.get('artifact_path'),
        'request_ok': record.get('ok'),
        'error_type': record.get('error_type'),
        'error_message': record.get('error_message'),
        'diagnostic_log': ({
            'path': metadata.get('diagnostic_log_path'),
            'sha256': metadata.get('diagnostic_log_sha256'),
            'bytes': metadata.get('diagnostic_log_bytes'),
        } if metadata.get('diagnostic_log_path') else None),
        'content_sha256': record.get('metrics', {}).get('content_sha256'),
        'file_artifacts': [item for item in raw_artifacts if item['kind'] == 'file'],
        'raw_artifacts': raw_artifacts,
        'raw_response_artifact': ({
            'path': metadata.get('raw_response_path'),
            'sha256': metadata.get('raw_response_sha256'),
            'bytes': metadata.get('raw_response_bytes'),
        } if metadata.get('raw_response_path') else ({
            'path': metadata.get('raw_stdout_path'),
            'sha256': metadata.get('raw_stdout_sha256'),
            'bytes': metadata.get('raw_stdout_bytes'),
        } if metadata.get('raw_stdout_path') else None)),
        'answer_metadata_sha256': hashlib.sha256(json.dumps(
            review_answer_metadata(record), ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest(),
    }


def _machine_verdict(source, record, case, reviewed=None, *, revision_checks=None):
    """Read current scores normally and frozen historical scores from their bound review."""
    try:
        return ('pass' if task_passed(
            record['ok'], record['metrics'], case.get('task_kind', 'body')) else 'fail')
    except ValueError as error:
        if str(error) != (
                'Historical scoring metrics require their frozen scorer or a complete offline rescore'):
            raise
        from .reference_lifecycle import valid_scoring_revision_chain
        verdict = (reviewed or {}).get('machine_verdict')
        if verdict not in {'pass', 'fail'}:
            raise
        if revision_checks is None:
            revision_checks = {}
        if source not in revision_checks:
            revision_checks[source] = valid_scoring_revision_chain(source)
        if not revision_checks[source]:
            raise
        return verdict


def _require_result_review(source, dataset, records, review_document=None, *, anomaly_gate):
    required = dataset.get('comparison', {}).get('require_independent_result_review', False)
    path = source / 'result-review.json'
    if review_document is None and not path.is_file():
        if required:
            raise ValueError('Independent result review is required but missing')
        return None
    review = review_document if review_document is not None else json.loads(path.read_text())
    if review.get('schema_version') != 1:
        raise ValueError('Unsupported result review schema')
    protocol = review.get('review_protocol', {})
    if required and protocol.get('machine_judgments_withheld') is not True:
        raise ValueError('Independent result review was not blinded to machine judgments')
    bindings = review.get('bindings', {})
    if bindings.get('dataset_sha256') != _sha256(source/'dataset.json'):
        raise ValueError('Result review is bound to a different dataset')
    if bindings.get('results_sha256') != _sha256(source/'results.jsonl'):
        raise ValueError('Result review is bound to different results')
    scope = protocol.get('scope', 'all')
    if scope not in {'all','machine-failures'}:
        raise ValueError('Unsupported result review scope')
    declared_scope = dataset.get('comparison', {}).get('independent_result_review_scope')
    if declared_scope is not None and declared_scope not in {'all', 'machine-failures'}:
        raise ValueError('Unsupported declared result review scope')
    if declared_scope == 'all' and scope != 'all':
        raise ValueError('The declared full review must include successful and failed attempts')
    anomaly_attempts = set()
    if anomaly_gate and dataset.get('comparison', {}).get('require_anomaly_review', False):
        from .anomaly_review import detect_anomalies
        reviewer = {row.get('attempt_id'): row.get('reviewer_anomalies', [])
                    for row in review.get('attempts', [])}
        anomaly_attempts = set(detect_anomalies(source, records, dataset, reviewer))
    expected = {r['attempt_id'] for r in records
                if scope == 'all' or task_passed(
                    r['ok'], r['metrics'],
                    next(c for c in dataset['cases'] if c['id']==r['case_id']).get('task_kind','body')) is False
                or r['attempt_id'] in anomaly_attempts}
    attempts = review.get('attempts', [])
    ids = [row.get('attempt_id') for row in attempts]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError('Result review must cover every attempt exactly once')
    unresolved = [row['attempt_id'] for row in attempts
                  if row.get('final_verdict') not in {'pass', 'fail'}]
    if unresolved:
        raise ValueError(f'Result review has unresolved attempts: {len(unresolved)}')
    by_id = {record['attempt_id']: record for record in records}
    cases = {case['id']: case for case in dataset.get('cases', [])}
    revision_checks = {}
    for row in attempts:
        record = by_id[row['attempt_id']]
        machine = _machine_verdict(
            source, record, cases[record['case_id']], reviewed=row, revision_checks=revision_checks)
        if row.get('machine_verdict') != machine:
            raise ValueError('Result review machine verdict no longer matches persisted scoring')
        if row.get('final_verdict') != machine:
            raise ValueError('Independent review found a scoring error; correct and re-review the results before publication')
    if anomaly_gate:
        require_anomaly_review(source, dataset, records, review)
    review['expected_attempts'] = len(expected)
    review['reviewed_attempts'] = len(ids)
    review['total_attempts'] = len(records)
    review['accepted_unreviewed_passes'] = len(records)-len(expected)
    review['disagreements'] = sum(row.get('agent_verdict') != row.get('machine_verdict')
                                  for row in attempts)
    review['unresolved'] = 0
    return review


def require_blind_result_review(source, dataset, records, review_document=None):
    """Validate a recorded blind review without requiring the later anomaly phase."""
    return _require_result_review(
        source, dataset, records, review_document, anomaly_gate=False)


def require_result_review(source, dataset, records, review_document=None):
    return _require_result_review(
        source, dataset, records, review_document, anomaly_gate=True)


class IndependentReviewRequired(ValueError):
    """The prior review is valid history but cannot certify revised scores."""


def rebind_unchanged_result_review(path, before_dataset, before_results,
                                   after_dataset, after_results, *, rebound_at):
    """Rebind a blind review only when its actual review inputs are unchanged."""
    if not path.is_file():
        return False
    review = json.loads(path.read_text())
    old_dataset_hash = hashlib.sha256(before_dataset).hexdigest()
    old_results_hash = hashlib.sha256(before_results).hexdigest()
    if review.get('bindings') != {
            'dataset_sha256': old_dataset_hash,
            'results_sha256': old_results_hash}:
        raise ValueError('Existing result review is not bound to scoring inputs')
    before_document = json.loads(before_dataset)
    after_document = json.loads(after_dataset)
    review_inputs = ('cases', 'coverage_plan')
    changed_review_inputs = [key for key in review_inputs
                             if before_document.get(key) != after_document.get(key)]
    if changed_review_inputs:
        raise IndependentReviewRequired(
            'Dataset review inputs changed; independent result review must be rerun: '
            + ', '.join(changed_review_inputs))
    allowed_metadata = {'id', 'name', 'comparison'}
    changed_top_level = sorted(
        key for key in set(before_document) | set(after_document)
        if before_document.get(key) != after_document.get(key)
    )
    if set(changed_top_level) - allowed_metadata:
        raise IndependentReviewRequired(
            'Dataset changed outside non-judgmental metadata; independent result review must be rerun')
    before_comparison = before_document.get('comparison', {})
    after_comparison = after_document.get('comparison', {})
    changed_comparison = {
        key for key in set(before_comparison) | set(after_comparison)
        if before_comparison.get(key) != after_comparison.get(key)
    }
    # Authored interpretation and implementation descriptions do not alter the
    # frozen task or raw answer. Their bytes are checked at the report boundary.
    presentation_fields = {'conversion', 'report_interpretation', 'implementation_provenance'}
    if changed_comparison - presentation_fields:
        raise IndependentReviewRequired(
            'Comparison semantics changed; independent result review must be rerun: '
            + ', '.join(sorted(changed_comparison - presentation_fields))
        )
    old_rows = {row['attempt_id']: row for row in parse_jsonl(before_results)}
    new_rows = {row['attempt_id']: row for row in parse_jsonl(after_results)}
    if old_rows.keys() != new_rows.keys():
        raise IndependentReviewRequired(
            'Attempt inventory changed; independent result review must be rerun')
    cases = {case['id']: case for case in after_document['cases']}
    scope = review.get('review_protocol', {}).get('scope', 'all')
    reviewed_ids = {row['attempt_id'] for row in review.get('attempts', [])}
    expected = set()
    for attempt_id, old in old_rows.items():
        new = new_rows[attempt_id]
        if review_input_identity(old) != review_input_identity(new):
            raise IndependentReviewRequired(
                'Review-visible input changed; independent result review must be rerun')
        kind = cases[new['case_id']].get('task_kind', 'body')
        old_verdict = task_passed(old['ok'], old['metrics'], kind)
        new_verdict = task_passed(new['ok'], new['metrics'], kind)
        if old_verdict != new_verdict:
            raise IndependentReviewRequired(
                'Machine verdict changed; independent result review must be rerun')
        if scope == 'all' or not new_verdict:
            expected.add(attempt_id)
    if reviewed_ids != expected:
        raise IndependentReviewRequired(
            'Review scope changed; independent result review must be rerun')
    machine = {row['attempt_id']: row for row in review['attempts']}
    for attempt_id in expected:
        verdict = 'pass' if task_passed(
            new_rows[attempt_id]['ok'], new_rows[attempt_id]['metrics'],
            cases[new_rows[attempt_id]['case_id']].get('task_kind', 'body')) else 'fail'
        if machine[attempt_id].get('machine_verdict') != verdict:
            raise IndependentReviewRequired(
                'Reviewed machine verdict changed; independent result review must be rerun')
    review['bindings'] = {
        'dataset_sha256': hashlib.sha256(after_dataset).hexdigest(),
        'results_sha256': hashlib.sha256(after_results).hexdigest(),
    }
    review.setdefault('review_protocol', {})['rebound_after_nonjudgmental_metadata_change'] = {
        'at': rebound_at, 'prior_results_sha256': old_results_hash,
        'prior_dataset_sha256': old_dataset_hash,
        'changed_top_level_fields': changed_top_level,
        'provider_calls': 0,
        'condition': 'cases, coverage, review-visible inputs, artifact hashes, scope and machine verdicts unchanged',
    }
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2)+'\n')
    return True


def archive_superseded_result_review(path: Path, *, before_dataset: bytes,
                                     before_results: bytes) -> dict:
    """Move a valid superseded review into immutable, hash-addressed history."""
    review_bytes = path.read_bytes()
    review = json.loads(review_bytes)
    old_dataset_hash = hashlib.sha256(before_dataset).hexdigest()
    old_results_hash = hashlib.sha256(before_results).hexdigest()
    if review.get('bindings') != {
            'dataset_sha256': old_dataset_hash,
            'results_sha256': old_results_hash}:
        raise ValueError('Existing result review is not bound to scoring inputs')
    history = path.parent/'review-history'
    history.mkdir(exist_ok=True)
    archive = history/f'result-review-{old_dataset_hash[:12]}-{old_results_hash[:12]}.json'
    if archive.exists():
        if archive.read_bytes() != review_bytes:
            raise ValueError(f'Review history collision: {archive.name}')
        path.unlink()
    else:
        path.replace(archive)
    return {
        'prior_result_review_path': archive.relative_to(path.parent).as_posix(),
        'prior_result_review_sha256': hashlib.sha256(review_bytes).hexdigest(),
        'independent_review_required': True,
    }


def _read_review_evidence(source, *, skip_blank=False):
    dataset_path = contained_path(source, "dataset.json", reject_symlinks=True)
    results_path = contained_path(source, "results.jsonl", reject_symlinks=True)
    dataset_bytes = dataset_path.read_bytes()
    return (
        json.loads(dataset_bytes),
        load_jsonl(results_path, skip_blank=skip_blank),
        {
            "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "results_sha256": file_sha256(results_path),
        },
    )


def write_review_document(target, review, *, before_replace=None):
    """Stage private review JSON and replace only an unchanged, unlinked destination."""
    if target.is_symlink():
        raise ValueError("Symlinked review output is forbidden")
    original = target.read_bytes() if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=".result-review-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write((json.dumps(review, ensure_ascii=False, indent=2) + "\n").encode())
        if before_replace is not None:
            before_replace()
        if target.is_symlink() or (target.read_bytes() if target.exists() else None) != original:
            raise ValueError("Review output changed before installation")
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install_result_review(evidence, review, *, anomaly_gate=True, skip_blank=False):
    """Validate a review against current evidence before replacing the installed file."""
    if not isinstance(review, dict):
        raise ValueError("An explicit result review document is required")
    dataset, records, bindings = _read_review_evidence(evidence, skip_blank=skip_blank)
    validator = require_result_review if anomaly_gate else require_blind_result_review
    validator(evidence, dataset, records, review)
    target = contained_path(evidence, "result-review.json", reject_symlinks=True)

    def unchanged():
        if _read_review_evidence(evidence, skip_blank=skip_blank)[2] != bindings:
            raise ValueError("Review evidence changed before installation")

    write_review_document(target, review, before_replace=unchanged)
    return target


def _revision_suffix(receipts, prior_bindings, current_bindings):
    """Return the complete revision suffix from a prior review to current bytes."""
    starts = [
        index
        for index, receipt in enumerate(receipts)
        if (receipt.get("input_dataset_sha256"), receipt.get("input_results_sha256"))
        == (prior_bindings.get("dataset_sha256"), prior_bindings.get("results_sha256"))
    ]
    if not starts:
        raise ValueError("Prior review is not bound to any scoring revision inputs")

    failures = []
    for start in starts:
        expected = (prior_bindings.get("dataset_sha256"), prior_bindings.get("results_sha256"))
        suffix = receipts[start:]
        for offset, receipt in enumerate(suffix):
            actual_input = (
                receipt.get("input_dataset_sha256"),
                receipt.get("input_results_sha256"),
            )
            if actual_input != expected:
                failures.append(f"Scoring revision chain is broken at receipt {start + offset + 1}")
                break
            expected = (receipt.get("output_dataset_sha256"), receipt.get("output_results_sha256"))
            if None in expected:
                failures.append(
                    f"Scoring revision receipt {start + offset + 1} has no output binding"
                )
                break
        else:
            if expected == (current_bindings["dataset_sha256"], current_bindings["results_sha256"]):
                return suffix
            failures.append("Scoring revision chain does not end at current evidence")
    raise ValueError(failures[0])


def merge_scoring_reviews(evidence: Path, prior_path: Path, supplemental_evidence: Path):
    dataset, records, current_bindings = _read_review_evidence(evidence)
    receipts = json.loads((evidence / "scoring-revisions.json").read_text())
    prior = json.loads(prior_path.read_text())
    suffix = _revision_suffix(receipts, prior.get("bindings", {}), current_bindings)
    changed_cases = {
        row["case_id"] for receipt in suffix for row in receipt.get("changed_cases", [])
    }
    if not changed_cases:
        raise ValueError("Scoring revision chain has no changed cases")
    current_cases = {case["id"]: case for case in dataset["cases"]}
    if not changed_cases <= set(current_cases):
        raise ValueError("Scoring revision changed cases are absent from current dataset")
    subset_dataset, subset_records, _ = _read_review_evidence(supplemental_evidence)
    supplemental = require_blind_result_review(
        supplemental_evidence, subset_dataset, subset_records
    )
    supplemental_protocol = supplemental.get("review_protocol", {})
    if supplemental_protocol.get("machine_judgments_withheld") is not True:
        raise ValueError("Supplemental review must be blinded to machine judgments")
    if supplemental_protocol.get("scope", "all") != "all":
        raise ValueError("Supplemental review must cover all changed attempts")
    subset_cases = {case["id"]: case for case in subset_dataset["cases"]}
    if set(subset_cases) != changed_cases or any(
        subset_cases[case_id] != current_cases[case_id] for case_id in changed_cases
    ):
        raise ValueError("Supplemental review must use every current changed task")
    expected = {r["attempt_id"] for r in records if r["case_id"] in changed_cases}
    supplied = {r["attempt_id"] for r in subset_records}
    if supplied != expected:
        raise ValueError("Supplemental review must contain every attempt for every changed case")
    current = {r["attempt_id"]: r for r in records}
    for row in subset_records:
        if row != current[row["attempt_id"]]:
            raise ValueError("Supplemental review was not run against current persisted results")
    replacements = {row["attempt_id"]: row for row in supplemental["attempts"]}
    if set(replacements) != expected:
        raise ValueError("Supplemental review must independently review every changed attempt")
    previous_attempts = prior.get("attempts", [])
    previous_ids = [row.get("attempt_id") for row in previous_attempts]
    if len(previous_ids) != len(set(previous_ids)) or set(previous_ids) != set(current):
        raise ValueError("Prior full review does not cover the current attempt inventory")
    retained = {
        row["attempt_id"]: row
        for row in previous_attempts
        if current[row["attempt_id"]]["case_id"] not in changed_cases
    }
    attempts = [
        replacements.get(row["attempt_id"], retained.get(row["attempt_id"])) for row in records
    ]
    if any(row is None for row in attempts):
        raise ValueError("Merged review does not cover the current attempt inventory")
    merged = json.loads(json.dumps(prior))
    merged["bindings"] = current_bindings
    merged["attempts"] = attempts
    merged.setdefault("review_protocol", {})["scope"] = "all"
    merged["review_protocol"]["scoring_rule_revision_review"] = {
        "changed_cases": sorted(changed_cases),
        "reviewed_attempts": len(replacements),
        "reviewer": supplemental["reviewer"],
        "machine_judgments_withheld": True,
        "revision_receipts": len(suffix),
    }
    merged["total_attempts"] = len(records)
    merged["expected_attempts"] = len(records)
    merged["reviewed_attempts"] = len(attempts)
    merged["accepted_unreviewed_passes"] = 0
    merged["disagreements"] = sum(
        row["agent_verdict"] != row["machine_verdict"] for row in attempts
    )
    merged["unresolved"] = sum(row["final_verdict"] not in {"pass", "fail"} for row in attempts)
    anomaly_gate = not dataset.get("comparison", {}).get("require_anomaly_review", False)
    return install_result_review(evidence, merged, anomaly_gate=anomaly_gate)


def _unique_by(rows, key, label):
    if not rows:
        raise ValueError(f"{label} must not be empty")
    values = [row.get(key) for row in rows]
    if None in values or len(values) != len(set(values)):
        raise ValueError(f"{label} must have unique non-empty {key} values")
    return dict(zip(values, rows))


def merge_refresh_reviews(
    evidence: Path,
    prior_evidence: Path,
    supplemental_evidence: Path,
    prior_review_path: Path | None = None,
):
    """Safely reuse reviews across an arbitrarily refreshed complete matrix.

    A prior per-attempt review is reusable only when the complete current case
    and every reviewer-visible evidence binding are unchanged, and the prior
    independent verdict still agrees with the current machine verdict. This
    ignores timestamps and diagnostic metrics that were never shown to the
    reviewer, while invalidating all providers for a changed task.
    """
    current_dataset, current_records, current_bindings = _read_review_evidence(evidence)
    prior_dataset, prior_records, prior_bindings = _read_review_evidence(prior_evidence)
    if prior_review_path is None:
        # The current anomaly detector may intentionally be stricter than the
        # one available when the prior blind review was recorded. Reuse only
        # its attempt-complete blind verdicts here; the assembled current
        # matrix must pass a fresh anomaly audit before publication.
        prior = require_blind_result_review(prior_evidence, prior_dataset, prior_records)
    else:
        prior = json.loads(prior_review_path.read_text())
        if prior.get("bindings") != prior_bindings:
            raise ValueError("Staged prior review is bound to different evidence")
        protocol = prior.get("review_protocol", {})
        if (
            protocol.get("machine_judgments_withheld") is not True
            or protocol.get("production_scorer_forbidden") is not True
            or protocol.get("direct_artifact_evidence_required") is not True
        ):
            raise ValueError("Staged prior review is not an independent blind review")
        if prior.get("unresolved") != 0:
            raise ValueError("Staged prior review has unresolved judgments")

    current_cases = _unique_by(current_dataset.get("cases", []), "id", "Current dataset")
    prior_cases = _unique_by(prior_dataset.get("cases", []), "id", "Prior dataset")
    current = _unique_by(current_records, "attempt_id", "Current results")
    previous = _unique_by(prior_records, "attempt_id", "Prior results")
    prior_attempts = _unique_by(prior.get("attempts", []), "attempt_id", "Prior review")
    if any(row.get("case_id") not in current_cases for row in current_records):
        raise ValueError("Current result refers to a task absent from the current dataset")

    prior_protocol = prior.get("review_protocol", {})
    if prior_protocol.get("scope", "all") != "all":
        raise ValueError("Prior review must be a complete full-scope review")
    if prior.get("accepted_unreviewed_passes", 0) != 0:
        raise ValueError("Prior review must not contain accepted unreviewed passes")

    reusable = set()
    revision_checks = {}
    for attempt_id, row in current.items():
        old = previous.get(attempt_id)
        case_id = row["case_id"]
        reviewed = prior_attempts.get(attempt_id)
        current_machine = _machine_verdict(
            prior_evidence, row, current_cases[case_id], reviewed=reviewed, revision_checks=revision_checks)
        if (
            old is not None
            and old.get("case_id") == case_id
            and prior_cases.get(case_id) == current_cases[case_id]
            and review_input_identity(old) == review_input_identity(row)
            and reviewed is not None
            and reviewed.get("final_verdict") == current_machine
        ):
            reusable.add(attempt_id)
    required = set(current) - reusable
    if not required:
        raise ValueError("Refresh has no attempts requiring supplemental review")

    subset_dataset, subset_records, _ = _read_review_evidence(supplemental_evidence)
    supplemental = require_blind_result_review(
        supplemental_evidence, subset_dataset, subset_records
    )
    if supplemental is None:
        raise ValueError("Supplemental independent result review is required")
    supplemental_protocol = supplemental.get("review_protocol", {})
    if supplemental_protocol.get("machine_judgments_withheld") is not True:
        raise ValueError("Supplemental review must be blinded to machine judgments")
    if supplemental_protocol.get("scope", "all") != "all":
        raise ValueError("Supplemental review must cover every non-reusable attempt")
    if supplemental.get("accepted_unreviewed_passes", 0) != 0:
        raise ValueError("Supplemental review must not contain accepted unreviewed passes")

    subset_cases = _unique_by(subset_dataset.get("cases", []), "id", "Supplemental dataset")
    required_cases = {current[attempt_id]["case_id"] for attempt_id in required}
    if set(subset_cases) != required_cases or any(
        subset_cases[case_id] != current_cases[case_id] for case_id in required_cases
    ):
        raise ValueError("Supplemental review must use every exact current required task")
    supplied_records = _unique_by(subset_records, "attempt_id", "Supplemental results")
    if set(supplied_records) != required:
        raise ValueError(
            "Supplemental review must contain exactly every non-reusable current attempt"
        )
    if any(row != current[attempt_id] for attempt_id, row in supplied_records.items()):
        raise ValueError("Supplemental review was not run against exact current result records")

    replacements = {row["attempt_id"]: row for row in supplemental["attempts"]}
    if set(replacements) != required:
        raise ValueError("Supplemental review must independently review every non-reusable attempt")
    retained = {attempt_id: prior_attempts[attempt_id] for attempt_id in reusable}
    attempts = [
        replacements.get(row["attempt_id"], retained.get(row["attempt_id"]))
        for row in current_records
    ]
    if any(row is None for row in attempts):
        raise ValueError("Merged review is missing an attempt")
    # Retained blind judgments may come from a staged review that already
    # identified a scorer error. After the scorer is corrected, carry the
    # independent judgment forward but bind machine/final verdict fields to
    # the current persisted score. Reviewer-visible inputs were checked above.
    normalized_attempts = []
    for record, reviewed in zip(current_records, attempts):
        source = prior_evidence if record['attempt_id'] in reusable else supplemental_evidence
        machine = _machine_verdict(
            source, record, current_cases[record['case_id']], reviewed=reviewed, revision_checks=revision_checks)
        reviewed = dict(reviewed)
        reviewed["machine_verdict"] = machine
        reviewed["final_verdict"] = machine
        if reviewed.get("agent_verdict") == machine:
            reviewed["adjudication"] = None
        elif not str(reviewed.get("adjudication") or "").strip():
            raise ValueError("Retained disagreement lacks adjudication: " + record["attempt_id"])
        normalized_attempts.append(reviewed)
    attempts = normalized_attempts
    merged = json.loads(json.dumps(prior))
    merged["bindings"] = current_bindings
    merged["attempts"] = attempts
    changed_task_cases = {
        case_id for case_id in current_cases if prior_cases.get(case_id) != current_cases[case_id]
    }
    rerun_only_attempts = required - {
        attempt_id
        for attempt_id in required
        if current[attempt_id]["case_id"] in changed_task_cases
    }
    merged.setdefault("review_protocol", {})["matrix_refresh_review"] = {
        "changed_task_cases": sorted(changed_task_cases),
        "rerun_only_attempts": sorted(rerun_only_attempts),
        "retained_attempts": len(retained),
        "reviewed_attempts": len(replacements),
        "reviewer": supplemental["reviewer"],
        "machine_judgments_withheld": True,
        "reuse_rule": "Only identical complete task definitions and identical reviewer-visible evidence bindings retained prior reviews; the prior adjudicated final verdict must still equal the current machine verdict.",
    }
    merged["total_attempts"] = len(current_records)
    merged["expected_attempts"] = len(current_records)
    merged["reviewed_attempts"] = len(attempts)
    merged["accepted_unreviewed_passes"] = 0
    merged["disagreements"] = sum(
        row["agent_verdict"] != row["machine_verdict"] for row in attempts
    )
    merged["unresolved"] = sum(row["final_verdict"] not in {"pass", "fail"} for row in attempts)
    merged["confirmed_scoring_errors"] = sum(
        row["final_verdict"] != row["machine_verdict"] for row in attempts
    )
    anomaly_gate = not current_dataset.get("comparison", {}).get("require_anomaly_review", False)
    return install_result_review(evidence, merged, anomaly_gate=anomaly_gate)


def rebase_review(
    current: Path,
    reviewed: Path,
    output: Path,
    adjudications: Path | None,
    *,
    review_file: Path | None = None,
    install: bool = False,
) -> dict:
    current_dataset, current_rows, current_bindings = _read_review_evidence(
        current, skip_blank=True
    )
    reviewed_dataset, reviewed_rows, reviewed_bindings = _read_review_evidence(
        reviewed, skip_blank=True
    )
    if current_dataset.get("cases") != reviewed_dataset.get("cases"):
        raise ValueError("Reviewed and current task definitions differ")
    current_by_id = {row["attempt_id"]: row for row in current_rows}
    reviewed_by_id = {row["attempt_id"]: row for row in reviewed_rows}
    if (
        len(current_by_id) != len(current_rows)
        or len(reviewed_by_id) != len(reviewed_rows)
        or current_by_id.keys() != reviewed_by_id.keys()
    ):
        raise ValueError("Reviewed and current attempt inventories differ")

    review_path = review_file or (reviewed / "result-review.json")
    prior = json.loads(review_path.read_text())
    if prior.get("bindings") != reviewed_bindings:
        raise ValueError("Prior review is not bound to reviewed evidence")
    if prior.get("review_protocol", {}).get("machine_judgments_withheld") is not True:
        raise ValueError("Prior review was not blinded to machine judgments")
    prior_attempts = {row.get("attempt_id"): row for row in prior.get("attempts", [])}
    if (
        len(prior_attempts) != len(prior.get("attempts", []))
        or prior_attempts.keys() != current_by_id.keys()
    ):
        raise ValueError("Prior review does not cover every current attempt")
    for attempt_id, row in current_by_id.items():
        if review_input_identity(row) != review_input_identity(reviewed_by_id[attempt_id]):
            raise ValueError("Reviewer-visible evidence changed: " + attempt_id)

    decisions = json.loads(adjudications.read_text()) if adjudications else {}
    cases = {case["id"]: case for case in current_dataset["cases"]}
    attempts = []
    for row in current_rows:
        attempt_id = row["attempt_id"]
        old = prior_attempts[attempt_id]
        agent = old.get("agent_verdict")
        if agent not in {"pass", "fail"} or not str(old.get("agent_evidence", "")).strip():
            raise ValueError("Prior independent judgment is incomplete: " + attempt_id)
        machine = (
            "pass"
            if task_passed(
                row["ok"], row["metrics"], cases[row["case_id"]].get("task_kind", "body")
            )
            else "fail"
        )
        adjudication = None
        if agent != machine:
            decision = decisions.get(attempt_id, {})
            if (
                decision.get("final_verdict") != machine
                or not str(decision.get("reason", "")).strip()
            ):
                raise ValueError(
                    "Current disagreement requires machine-consistent adjudication: " + attempt_id
                )
            adjudication = decision["reason"]
        attempts.append(
            {
                "attempt_id": attempt_id,
                "machine_verdict": machine,
                "agent_verdict": agent,
                "agent_evidence": old["agent_evidence"],
                "reviewer_anomalies": old.get("reviewer_anomalies", []),
                "final_verdict": machine,
                "adjudication": adjudication,
            }
        )

    result = json.loads(json.dumps(prior))
    result["bindings"] = current_bindings
    result["attempts"] = attempts
    result.setdefault("review_protocol", {})["score_only_rebase"] = {
        "prior_dataset_sha256": reviewed_bindings["dataset_sha256"],
        "prior_results_sha256": reviewed_bindings["results_sha256"],
        "condition": "Exact tasks, attempt inventory and every reviewer-visible evidence binding were unchanged; independent agent verdicts were retained.",
    }
    result["total_attempts"] = len(current_rows)
    result["expected_attempts"] = len(current_rows)
    result["reviewed_attempts"] = len(current_rows)
    result["accepted_unreviewed_passes"] = 0
    result["disagreements"] = sum(
        row["agent_verdict"] != row["machine_verdict"] for row in attempts
    )
    result["unresolved"] = 0
    protected = [
        current / "dataset.json",
        current / "results.jsonl",
        reviewed / "dataset.json",
        reviewed / "results.jsonl",
        review_path,
    ]
    if output.resolve() in {path.resolve() for path in protected}:
        raise ValueError("Review output must not overwrite its source evidence")
    installed = current / "result-review.json"
    if output.resolve() == installed.resolve() and not install:
        raise ValueError("Use --install when output is the installed result review")
    validator = require_result_review if install else require_blind_result_review
    validator(current, current_dataset, current_rows, result)
    if output.resolve() != installed.resolve():
        write_review_document(output, result)
    if install:
        install_result_review(current, result, skip_blank=True)
    return result


def merge_main():
    parser = argparse.ArgumentParser(
        description="Merge supplemental independent reviews after scoring or matrix revisions"
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--prior-review", type=Path)
    parser.add_argument("--supplemental-evidence", type=Path, required=True)
    parser.add_argument("--prior-evidence", type=Path)
    args = parser.parse_args()
    if args.prior_evidence:
        print(
            merge_refresh_reviews(
                args.evidence, args.prior_evidence, args.supplemental_evidence, args.prior_review
            )
        )
    else:
        if not args.prior_review:
            parser.error("--prior-review is required without --prior-evidence")
        print(merge_scoring_reviews(args.evidence, args.prior_review, args.supplemental_evidence))


def rebase_main():
    parser = argparse.ArgumentParser(
        description="Rebind completed blind judgments after score-only changes"
    )
    parser.add_argument("--current-evidence", type=Path, required=True)
    parser.add_argument("--reviewed-evidence", type=Path, required=True)
    parser.add_argument(
        "--review-file",
        type=Path,
        help="Completed review file when it has not been installed in reviewed evidence",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    review = rebase_review(
        args.current_evidence.resolve(),
        args.reviewed_evidence.resolve(),
        args.output,
        args.adjudications,
        review_file=args.review_file.resolve() if args.review_file else None,
        install=args.install,
    )
    print(
        f"reviewed={review['reviewed_attempts']} disagreements={review['disagreements']} unresolved=0"
    )


def merge_reviews(evidence: Path, shard_paths: list[Path], output: Path, *,
                  prior_review: Path | None = None,
                  adjudications: Path | None = None, install: bool = False,
                  allow_confirmed_errors: bool = False) -> dict:
    dataset, records, bindings = _read_review_evidence(evidence, skip_blank=True)
    installed = contained_path(evidence, "result-review.json", reject_symlinks=True)
    output = contained_path(output.parent, output.name, reject_symlinks=True)
    if output.is_relative_to(evidence.resolve()) and not (install and output == installed):
        raise ValueError("Review output must be outside evidence unless installing result-review.json")
    inputs = [*shard_paths, *([prior_review] if prior_review else []),
              *([adjudications] if adjudications else [])]
    snapshots = {}
    for path in inputs:
        safe = contained_path(path.parent, path.name, reject_symlinks=True)
        if output == safe and output != installed:
            raise ValueError("Review output must not overwrite an input")
        snapshots[safe] = safe.read_bytes()
    original_review = installed.read_bytes() if installed.exists() else None

    def unchanged():
        if _read_review_evidence(evidence, skip_blank=True)[2] != bindings:
            raise ValueError("Review evidence changed during merge")
        for path, original in snapshots.items():
            safe = contained_path(path.parent, path.name, reject_symlinks=True)
            if safe.read_bytes() != original:
                raise ValueError("Review input changed during merge")
        current = contained_path(evidence, "result-review.json", reject_symlinks=True)
        if (current.read_bytes() if current.exists() else None) != original_review:
            raise ValueError("Installed review changed during merge")
    cases = {case["id"]: case for case in dataset["cases"]}
    by_id = {record["attempt_id"]: record for record in records}
    if len(by_id) != len(records):
        raise ValueError("Evidence contains duplicate attempt IDs")

    prior_rows: dict[str, dict] = {}
    prior_model = None
    protocols = []
    reviewers = []
    prior_independent = False
    if prior_review is not None:
        current_review = installed
        if _sha256(prior_review) != _sha256(current_review):
            raise ValueError("Prior review must be the validated review installed in evidence")
        prior = require_result_review(evidence, dataset, records)
        if prior["review_protocol"].get("scope") != "machine-failures":
            raise ValueError("Prior review must cover exactly the machine-failure scope")
        prior_rows = {row["attempt_id"]: row for row in prior["attempts"]}
        prior_model = prior.get("reviewer", {}).get("model")
        protocols.append(prior["review_protocol"])
        reviewers.append(prior.get("reviewer", {}))
        prior_independent = (
            prior["review_protocol"].get("production_scorer_forbidden") is True
            and prior["review_protocol"].get("direct_artifact_evidence_required") is True
        )
        if not prior_independent:
            raise ValueError("Prior review does not enforce scorer-independent artifact evidence")

    supplied: dict[str, dict] = {}
    shard_metadata = []
    for path in shard_paths:
        safe = contained_path(path.parent, path.name, reject_symlinks=True)
        shard = json.loads(snapshots[safe])
        if shard.get("schema_version") == 1 and shard.get("review_protocol", {}).get("scope") == "machine-passes":
            if (shard["review_protocol"].get("machine_judgments_withheld") is not True
                    or shard["review_protocol"].get("production_scorer_forbidden") is not True
                    or shard["review_protocol"].get("direct_artifact_evidence_required") is not True):
                raise ValueError(f"Review shard is not scorer-independent: {path}")
            if shard.get("bindings") != bindings:
                raise ValueError(f"Review shard is bound to different evidence: {path}")
            providers = shard.get("review_protocol", {}).get("provider_filter", [])
            rows = [{"attempt_id": row.get("attempt_id"),
                     "reviewer_verdict": row.get("agent_verdict"),
                     "rationale": row.get("agent_evidence", "")}
                    for row in shard.get("attempts", [])]
        else:
            raise ValueError(f"Unexpected reviewer in {path}")
        if not isinstance(shard.get("reviewer"), dict):
            raise ValueError(f"Review shard must declare reviewer metadata: {path}")
        protocols.append(shard["review_protocol"])
        reviewers.append(shard["reviewer"])
        shard_metadata.append({
            "path": path.name,
            "providers": providers,
            "reviewed": len(rows),
            "model": shard.get("reviewer", {}).get("model", "task runtime default"),
            "sha256": hashlib.sha256(snapshots[safe]).hexdigest(),
        })
        for row in rows:
            attempt_id = row.get("attempt_id")
            if attempt_id in supplied:
                raise ValueError(f"Duplicate reviewed attempt: {attempt_id}")
            supplied[attempt_id] = row

    expected = by_id.keys() - prior_rows.keys()
    if supplied.keys() != expected:
        missing = sorted(expected - supplied.keys())
        extra = sorted(supplied.keys() - expected)
        raise ValueError(f"Review coverage differs; missing={missing[:5]} extra={extra[:5]}")

    adjudicated = json.loads(snapshots[adjudications.resolve()]) if adjudications else {}
    attempts = []
    for record in records:
        if record["attempt_id"] in prior_rows:
            attempts.append(prior_rows[record["attempt_id"]])
            continue
        reviewed = supplied[record["attempt_id"]]
        machine = "pass" if task_passed(
            record["ok"], record["metrics"],
            cases[record["case_id"]].get("task_kind", "body")) else "fail"
        agent = reviewed.get("reviewer_verdict")
        if agent not in {"pass", "fail"}:
            raise ValueError(f"Unresolved agent verdict: {record['attempt_id']}")
        rationale = reviewed.get("rationale", "").strip()
        if not rationale:
            raise ValueError(f"Review evidence is empty: {record['attempt_id']}")
        adjudication = None
        if agent != machine:
            decision = adjudicated.get(record["attempt_id"], {})
            if decision.get("final_verdict") not in {"pass", "fail"}:
                raise ValueError(
                    f"Review disagreement requires adjudication: "
                    f"{record['attempt_id']} agent={agent} machine={machine}")
            if decision["final_verdict"] != machine and not allow_confirmed_errors:
                raise ValueError(
                    f"Confirmed scoring disagreement requires correction and re-review: "
                    f"{record['attempt_id']} agent={agent} machine={machine}")
            adjudication = decision.get("reason")
        final = ((adjudicated.get(record["attempt_id"], {}) or {}).get("final_verdict")
                 if agent != machine else machine)
        attempts.append({
            "attempt_id": record["attempt_id"],
            "machine_verdict": machine,
            "agent_verdict": agent,
            "agent_evidence": rationale,
            "final_verdict": final,
            "adjudication": adjudication,
        })

    models = sorted({row["model"] for row in shard_metadata} | ({prior_model} if prior_model else set()))
    confirmed_errors = [row["attempt_id"] for row in attempts
                        if row["final_verdict"] != row["machine_verdict"]]
    review = {
        "schema_version": 1,
        "bindings": bindings,
        "reviewer": {
            "model": ", ".join(models),
            "sources": reviewers,
        },
        "review_protocol": {
            "machine_judgments_withheld": True,
            "production_scorer_forbidden": True,
            "direct_artifact_evidence_required": True,
            **{key: all(protocol.get(key) is True for protocol in protocols)
               for key in ("complete_artifact_paths_supplied", "source_answer_metadata_supplied",
                           "declared_acceptance_evidence_supplied")},
            "provider_filter": [],
            "scope": "all",
            "shards": shard_metadata,
        },
        "total_attempts": len(records),
        "accepted_unreviewed_passes": 0,
        "expected_attempts": len(records),
        "reviewed_attempts": len(attempts),
        "disagreements": sum(row["agent_verdict"] != row["machine_verdict"] for row in attempts),
        "unresolved": 0,
        "confirmed_scoring_errors": len(confirmed_errors),
        "attempts": attempts,
    }
    if install and confirmed_errors:
        raise ValueError("Cannot install a review with confirmed scoring errors")
    # Confirmed errors are an explicit diagnostic document, never installed certification.
    if not confirmed_errors:
        require_result_review(evidence, dataset, records, review)
    unchanged()
    write_review_document(output, review, before_replace=unchanged)
    if install and output != installed:
        unchanged()
        install_result_review(evidence, review, skip_blank=True)
    return review


def merge_shards_main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Merge hash-bound independent review shards")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--shard", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prior-review", type=Path)
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--allow-confirmed-errors", action="store_true",
                        help="Write a non-installable staging review before correcting confirmed score errors")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args(argv)
    review = merge_reviews(
        args.evidence, args.shard, args.output,
        prior_review=args.prior_review, adjudications=args.adjudications,
        install=args.install, allow_confirmed_errors=args.allow_confirmed_errors,
    )
    print(f"reviewed={len(review['attempts'])} disagreements={review['disagreements']} unresolved=0")



def refresh_review_attempt_ids(evidence: Path, prior_evidence: Path) -> set[str]:
    """Return attempts whose task or reviewer-visible evidence changed."""
    current_dataset, current_records, _ = _read_review_evidence(evidence, skip_blank=True)
    prior_dataset, prior_records, _ = _read_review_evidence(prior_evidence, skip_blank=True)
    # Selecting the supplemental blind-review scope precedes the post-blind
    # anomaly gate. Requiring that later gate here creates a circular workflow.
    prior_review = require_blind_result_review(
        prior_evidence, prior_dataset, prior_records)
    current_cases = _unique_by(current_dataset["cases"], "id", "Current dataset")
    prior_cases = _unique_by(prior_dataset["cases"], "id", "Prior dataset")
    _unique_by(current_records, "attempt_id", "Current results")
    previous = _unique_by(prior_records, "attempt_id", "Prior results")
    prior_judgments = {row["attempt_id"]: row for row in prior_review["attempts"]}
    return {
        row["attempt_id"] for row in current_records
        if prior_cases.get(row["case_id"]) != current_cases[row["case_id"]]
        or previous.get(row["attempt_id"]) is None
        or prior_judgments.get(row["attempt_id"]) is None
        or review_input_identity(previous[row["attempt_id"]]) != review_input_identity(row)
    }


def prepare_review_subset(evidence: Path, output: Path, *, case_ids=None,
                          prior_evidence: Path | None = None):
    """Stage a complete hash-verified supplemental scope before exposing its directory."""
    from .artifacts import materialize_bound_artifacts
    from .safety import evidence_tree_identity, stage_evidence_revision

    if bool(case_ids) == bool(prior_evidence):
        raise ValueError("choose exactly one of case_ids or prior_evidence")
    prior_identity = evidence_tree_identity(prior_evidence) if prior_evidence else None

    def prepare(staging):
        dataset, all_records, _ = _read_review_evidence(evidence, skip_blank=True)
        _unique_by(dataset["cases"], "id", "Current dataset")
        _unique_by(all_records, "attempt_id", "Current results")
        selected_attempts = (
            refresh_review_attempt_ids(evidence, prior_evidence)
            if prior_evidence else None
        )
        requested = ({row["case_id"] for row in all_records
                      if row["attempt_id"] in selected_attempts}
                     if selected_attempts is not None else set(case_ids))
        cases = [case for case in dataset.get("cases", []) if case.get("id") in requested]
        found = {case["id"] for case in cases}
        if found != requested:
            raise ValueError("Unknown case IDs: " + ", ".join(sorted(requested - found)))
        records = [row for row in all_records
                   if (row["attempt_id"] in selected_attempts
                       if selected_attempts is not None
                       else row.get("case_id") in requested)]
        providers = {row["provider"] for row in all_records}
        if selected_attempts is not None:
            expected = len(selected_attempts)
            if ({row["attempt_id"] for row in records} != selected_attempts
                    or len(records) != expected):
                raise ValueError(f"Refresh subset is incomplete: expected {expected}, found {len(records)}")
        else:
            expected = len(requested) * len(providers)
            coverage = {(row["provider"], row["case_id"]) for row in records}
            expected_coverage = {(provider, case_id) for provider in providers for case_id in requested}
            if (coverage != expected_coverage or len(records) != expected
                    or len({row["attempt_id"] for row in records}) != expected):
                raise ValueError(f"Subset is incomplete: expected {expected}, found {len(records)}")

        staging.mkdir()
        subset = dict(dataset)
        subset["cases"] = cases
        comparison = dict(dataset.get("comparison", {}))
        comparison["require_independent_result_review"] = True
        subset["comparison"] = comparison
        (staging / "dataset.json").write_text(json.dumps(subset, ensure_ascii=False, indent=2) + "\n")
        (staging / "results.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records))
        materialize_bound_artifacts(staging, evidence, records)
        if prior_evidence and evidence_tree_identity(prior_evidence) != prior_identity:
            raise ValueError("Prior review evidence changed during subset preparation")
        return {"cases": len(cases), "attempts": len(records)}

    return stage_evidence_revision(evidence, output, prepare)


def prepare_subset_main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare hash-verified supplemental review evidence")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--case-id", action="append")
    selection.add_argument("--prior-evidence", type=Path)
    args = parser.parse_args(argv)
    counts = prepare_review_subset(args.evidence, args.output, case_ids=args.case_id,
                                   prior_evidence=args.prior_evidence)
    print(f"cases={counts['cases']} attempts={counts['attempts']} output={args.output}")


def rebind_metadata_review(evidence: Path, prior_dataset: Path, *, rebound_at=None):
    """Rebind neutral metadata in a validated bundle, preserving immutable history."""
    import shutil
    from datetime import datetime, timezone

    from .artifacts import verify_record_artifacts
    from .assembly import bind_assembled_scoring_revision
    from .reference_inputs import require_mutable_inputs
    from .reference_lifecycle import valid_reference_revision, valid_scoring_revision
    from .safety import commit_staged_evidence, evidence_tree_identity

    before = evidence_tree_identity(evidence)
    evidence = evidence.resolve()
    require_mutable_inputs(evidence / "dataset.json")
    prior_path = contained_path(prior_dataset.parent, prior_dataset.name, reject_symlinks=True)
    prior_bytes = prior_path.read_bytes()
    current_bytes = (evidence / "dataset.json").read_bytes()
    current_document, records, _ = _read_review_evidence(evidence)
    _unique_by(current_document["cases"], "id", "Current dataset")
    _unique_by(records, "attempt_id", "Current results")
    prior_document = json.loads(prior_bytes)
    changed_fields = sorted(key for key in set(prior_document) | set(current_document)
                            if prior_document.get(key) != current_document.get(key))
    if not changed_fields:
        raise ValueError("Dataset metadata is unchanged; rebinding is unnecessary")
    rebound_at = rebound_at or datetime.now(timezone.utc).isoformat()
    work = Path(tempfile.mkdtemp(prefix=f".{evidence.name}-metadata-history-", dir=evidence.parent))
    staged, backup = work / "staged", work / "original"
    committed = False
    try:
        shutil.copytree(evidence, staged)
        # Validate the complete predecessor against its actual dataset before
        # reusing a review or extending its receipt chain.
        (staged / "dataset.json").write_bytes(prior_bytes)
        require_result_review(staged, prior_document, records)
        if not valid_scoring_revision(staged):
            raise ValueError("Scoring revision history is missing, invalid or unbound")
        reference_path = staged / "reference-revisions.json"
        if reference_path.exists() and not valid_reference_revision(staged):
            raise ValueError("Reference revision history is invalid or unbound")
        native_cases = {c['id']: c for c in prior_document['cases']}
        for record in records:
            verify_record_artifacts(staged, record, native_cases=native_cases)
        (staged / "dataset.json").write_bytes(current_bytes)
        results_bytes = (staged / "results.jsonl").read_bytes()
        if not rebind_unchanged_result_review(
            staged / "result-review.json", prior_bytes, results_bytes,
            current_bytes, results_bytes,
            rebound_at=rebound_at,
        ):
            raise ValueError("result-review.json is missing")
        if not bind_assembled_scoring_revision(staged):
            raise ValueError("Metadata update did not extend its scoring revision history")
        history_path = staged / "scoring-revisions.json"
        history = json.loads(history_path.read_bytes())
        history[-1]["scored_at"] = rebound_at
        history[-1]["metadata_change"] = {"changed_top_level_fields": changed_fields}
        history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n")
        if not valid_scoring_revision(staged):
            raise ValueError("Metadata update would leave an invalid scoring history")
        if reference_path.exists() and not valid_reference_revision(staged):
            raise ValueError("Metadata update would leave an invalid reference history")
        checked_prior = contained_path(prior_dataset.parent, prior_dataset.name, reject_symlinks=True)
        if evidence_tree_identity(evidence) != before or checked_prior.read_bytes() != prior_bytes:
            raise ValueError("Metadata rebinding inputs changed during staging")
        commit_staged_evidence(evidence, staged, backup)
        committed = True
        return evidence / "result-review.json"
    finally:
        # Retain the predecessor on success and the sole original if recovery fails.
        if not committed and not backup.exists():
            shutil.rmtree(work, ignore_errors=True)


def rebind_metadata_main(argv=None):
    parser = argparse.ArgumentParser(description="Rebind a blind review after neutral metadata changes")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--prior-dataset", type=Path, required=True)
    args = parser.parse_args(argv)
    print(rebind_metadata_review(args.evidence, args.prior_dataset))
