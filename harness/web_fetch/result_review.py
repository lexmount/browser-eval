"""Validate an independently produced, attempt-complete result review."""
import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from harness.record_io import parse_jsonl

from .anomaly_review import require_anomaly_review
from .metrics import task_passed
from .safety import contained_path

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
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    for row in attempts:
        record = by_id[row['attempt_id']]
        machine = ('pass' if task_passed(record['ok'], record['metrics'],
                   cases[record['case_id']].get('task_kind', 'body')) else 'fail')
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
    if changed_comparison - {'conversion'}:
        raise IndependentReviewRequired(
            'Comparison semantics changed; independent result review must be rerun: '
            + ', '.join(sorted(changed_comparison - {'conversion'}))
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
    dataset_bytes = contained_path(source, "dataset.json", reject_symlinks=True).read_bytes()
    results_bytes = contained_path(source, "results.jsonl", reject_symlinks=True).read_bytes()
    return (
        json.loads(dataset_bytes),
        parse_jsonl(results_bytes, skip_blank=skip_blank),
        {
            "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "results_sha256": hashlib.sha256(results_bytes).hexdigest(),
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
    for attempt_id, row in current.items():
        old = previous.get(attempt_id)
        case_id = row["case_id"]
        current_machine = (
            "pass"
            if task_passed(
                row["ok"], row["metrics"], current_cases[case_id].get("task_kind", "body")
            )
            else "fail"
        )
        if (
            old is not None
            and old.get("case_id") == case_id
            and prior_cases.get(case_id) == current_cases[case_id]
            and review_input_identity(old) == review_input_identity(row)
            and prior_attempts.get(attempt_id, {}).get("final_verdict") == current_machine
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
        machine = (
            "pass"
            if task_passed(
                record["ok"],
                record["metrics"],
                current_cases[record["case_id"]].get("task_kind", "body"),
            )
            else "fail"
        )
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
