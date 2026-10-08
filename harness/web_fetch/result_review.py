"""Validate an independently produced, attempt-complete result review."""
import hashlib
import json
from pathlib import Path

from harness.record_io import parse_jsonl

from .anomaly_review import require_anomaly_review
from .metrics import task_passed

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
