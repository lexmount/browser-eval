"""Validate an independently produced, attempt-complete result review."""
import hashlib
import json
from .metrics import task_passed


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_result_review(source, dataset, records):
    required = dataset.get('comparison', {}).get('require_independent_result_review', False)
    path = source / 'result-review.json'
    if not path.is_file():
        if required:
            raise ValueError('Independent result review is required but missing')
        return None
    review = json.loads(path.read_text())
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
    expected = {r['attempt_id'] for r in records
                if scope == 'all' or task_passed(
                    r['ok'], r['metrics'],
                    next(c for c in dataset['cases'] if c['id']==r['case_id']).get('task_kind','body')) is False}
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
    review['expected_attempts'] = len(expected)
    review['reviewed_attempts'] = len(ids)
    review['total_attempts'] = len(records)
    review['accepted_unreviewed_passes'] = len(records)-len(expected)
    review['disagreements'] = sum(row.get('agent_verdict') != row.get('machine_verdict')
                                  for row in attempts)
    review['unresolved'] = 0
    return review
