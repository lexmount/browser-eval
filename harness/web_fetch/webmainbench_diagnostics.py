"""Shared investigation signals; these do not change official scores."""
import re
import hashlib
import json

from .safety import contained_path
from .scoring_regressions import valid_correction_provenance

ERROR_PAGE = re.compile(
    r"(?:^#\s*Error response\b|^Error code:\s*404\b|ERR[_\\]ACCESS[_\\]DENIED\b|"
    r"^#\s*无法访问此网站\b|^This site can.t be reached\b)", re.I | re.M,
)


def require_valid_teds_for_release(records):
    """A reproduced official defect may be inspected locally, never released."""
    invalid = []
    for row in records:
        details = row.get('metrics', {}).get('table_TEDS', {}).get('details', {})
        if all(key in details for key in ('predicted_nodes', 'groundtruth_nodes', 'edit_distance')):
            lower_bound = abs(details['predicted_nodes'] - details['groundtruth_nodes'])
            if details['edit_distance'] + 1e-9 < lower_bound:
                invalid.append(row.get('attempt_id', row.get('case_id', 'unknown')))
    if invalid:
        raise ValueError(f'Publication blocked: {len(invalid)} impossible TEDS distances; '
                         'resolve the scorer defect and revalidate affected scores before release')


def validate_teds_revision(root):
    """Verify the self-contained revision chain before using corrected scores."""
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    receipt_path = contained_path(root, 'teds-correction.json')
    receipt = json.loads(receipt_path.read_text())
    review = json.loads(contained_path(root, 'teds-correction-review.json').read_text())
    if not valid_correction_provenance(receipt) or receipt.get('provider_calls') != 0:
        raise ValueError('Invalid TEDS correction provenance')
    if review.get('status') != 'passed' or review.get('logical_results') != 1000:
        raise ValueError('TEDS correction review incomplete')
    if (digest(receipt_path) != review.get('correction_receipt_sha256')
            or digest(root / 'results.jsonl') != receipt.get('output_results_sha256')
            or receipt['output_results_sha256'] != review.get('output_results_sha256')
            or digest(root / 'original-results.jsonl') != receipt.get('input_results_sha256')
            or digest(root / 'dataset.json') != receipt.get('dataset_sha256')):
        raise ValueError('TEDS correction evidence hash mismatch')
    for path, expected in receipt['source_code_sha256'].items():
        if digest(contained_path(root, 'correction-source/' + path)) != expected:
            raise ValueError('TEDS correction source hash mismatch')
    original = [json.loads(line) for line in (root / 'original-results.jsonl').read_text().splitlines()]
    current = [json.loads(line) for line in (root / 'results.jsonl').read_text().splitlines()]
    if len(original) != 1000 or len(current) != 1000:
        raise ValueError('TEDS correction scope mismatch')
    if len({row['attempt_id'] for row in current}) != 1000:
        raise ValueError('Duplicate corrected result')
    for before, after in zip(original, current):
        if after.get('official_metrics') != before.get('metrics'):
            raise ValueError('Official metrics changed in correction')
        left = {k: v for k, v in before.items() if k != 'metrics'}
        right = {k: v for k, v in after.items() if k not in ('metrics', 'official_metrics')}
        if left != right:
            raise ValueError('Provider evidence changed in scoring correction')
        for metric in ('text_edit', 'code_edit', 'formula_edit', 'table_edit'):
            if before['metrics'].get(metric) != after['metrics'].get(metric):
                raise ValueError('Unrelated metric changed in TEDS correction')
        if before['metrics'].get('table_TEDS', {}).get('success') != after['metrics'].get('table_TEDS', {}).get('success'):
            raise ValueError('TEDS correction changed the scored population')
    require_valid_teds_for_release(current)
    return receipt, review
