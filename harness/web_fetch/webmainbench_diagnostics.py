"""Shared investigation signals; these do not change official scores."""
import hashlib
import json
import re

from harness.record_io import load_jsonl

from .safety import contained_path
from .scoring_regressions import valid_correction_provenance
from .webmainbench_latency_correction import prior_results_path

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
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob('*')):
        raise ValueError('Refusing symlinked TEDS correction evidence')
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    receipt_path = contained_path(root, 'teds-correction.json')
    reference_receipt_path = root / 'reference-correction.json'
    scoring_receipt_path = root / 'scoring-correction.json'
    if reference_receipt_path.is_file():
        teds_output_path = contained_path(root, 'pre-reference-correction-results.jsonl')
        teds_dataset_path = contained_path(root, 'pre-reference-correction-dataset.json')
    elif scoring_receipt_path.is_file():
        teds_output_path = contained_path(root, 'pre-scoring-correction-results.jsonl')
        teds_dataset_path = contained_path(root, 'pre-scoring-correction-dataset.json')
    else:
        teds_output_path = prior_results_path(root)
        teds_dataset_path = contained_path(root, 'dataset.json')
    receipt = json.loads(receipt_path.read_text())
    review = json.loads(contained_path(root, 'teds-correction-review.json').read_text())
    if not valid_correction_provenance(receipt) or receipt.get('provider_calls') != 0:
        raise ValueError('Invalid TEDS correction provenance')
    expected_count = receipt.get('scope', {}).get('logical_results')
    if review.get('status') != 'passed' or review.get('logical_results') != expected_count:
        raise ValueError('TEDS correction review incomplete')
    if (digest(receipt_path) != review.get('correction_receipt_sha256')
            or digest(teds_output_path) != receipt.get('output_results_sha256')
            or receipt['output_results_sha256'] != review.get('output_results_sha256')
            or digest(root / 'original-results.jsonl') != receipt.get('input_results_sha256')
            or digest(teds_dataset_path) != receipt.get('dataset_sha256')):
        raise ValueError('TEDS correction evidence hash mismatch')
    for path, expected in receipt['source_code_sha256'].items():
        if digest(contained_path(root / 'correction-source', path)) != expected:
            raise ValueError('TEDS correction source hash mismatch')
    original = load_jsonl(root / 'original-results.jsonl')
    current = load_jsonl(teds_output_path)
    if len(original) != expected_count or len(current) != expected_count:
        raise ValueError('TEDS correction scope mismatch')
    if len({row['attempt_id'] for row in current}) != expected_count:
        raise ValueError('Duplicate corrected result')
    for before, after in zip(original, current):
        if after.get('official_metrics') != before.get('official_metrics', before.get('metrics')):
            raise ValueError('Official metrics changed in correction')
        left = {k: v for k, v in before.items() if k not in ('metrics', 'official_metrics')}
        right = {k: v for k, v in after.items() if k not in ('metrics', 'official_metrics')}
        if left != right:
            raise ValueError('Provider evidence changed in scoring correction')
        for metric in ('text_edit', 'code_edit', 'formula_edit', 'table_edit'):
            if before['metrics'].get(metric) != after['metrics'].get(metric):
                raise ValueError('Unrelated metric changed in TEDS correction')
        prior_teds = before['metrics'].get('table_TEDS', {})
        current_teds = after['metrics'].get('table_TEDS', {})
        if prior_teds.get('success') != current_teds.get('success') and not (
            prior_teds.get('success') and not current_teds.get('success') and
            'Exact tree calculation exceeds safe workspace bound' in current_teds.get('error', '')
        ):
            raise ValueError('Unexpected TEDS scoring population change')
    require_valid_teds_for_release(current)
    return receipt, review


def validate_reference_revision(root):
    """Verify a score-only reference correction chained after TEDS correction."""
    receipt_path = contained_path(root, 'reference-correction.json')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    prior_results = contained_path(root, 'pre-reference-correction-results.jsonl')
    prior_dataset = contained_path(root, 'pre-reference-correction-dataset.json')
    # A later shared-population scoring correction retains the exact output of
    # this stage as its immutable predecessor.
    current_results = contained_path(
        root, 'pre-scoring-correction-results.jsonl'
        if (root / 'scoring-correction.json').is_file() else 'results.jsonl')
    current_dataset = contained_path(
        root, 'pre-scoring-correction-dataset.json'
        if (root / 'scoring-correction.json').is_file() else 'dataset.json')
    if (receipt.get('schema_version') != 1
            or receipt.get('kind') != 'webmainbench_reference_correction_v1'
            or receipt.get('provider_calls') != 0
            or receipt.get('input_results_sha256') != hashlib.sha256(prior_results.read_bytes()).hexdigest()
            or receipt.get('output_results_sha256') != hashlib.sha256(current_results.read_bytes()).hexdigest()
            or receipt.get('prior_evidence_dataset_sha256') != hashlib.sha256(prior_dataset.read_bytes()).hexdigest()
            or receipt.get('output_evidence_dataset_sha256') != hashlib.sha256(current_dataset.read_bytes()).hexdigest()):
        raise ValueError('Invalid WebMainBench reference correction binding')
    previous = load_jsonl(prior_results)
    current = load_jsonl(current_results)
    if len(previous) != receipt.get('logical_results') or len(current) != len(previous):
        raise ValueError('Reference correction result scope mismatch')
    corrections = {item['track_id']: item for item in receipt.get('corrections', [])}
    affected = 0
    for before, after in zip(previous, current):
        if before['attempt_id'] != after['attempt_id']:
            raise ValueError('Reference correction reordered logical results')
        if before['case_id'] in corrections:
            affected += 1
            correction = corrections[before['case_id']]
            if (before['reference_sha256'] != correction['original_reference_sha256']
                    or after['reference_sha256'] != correction['replacement_reference_sha256']
                    or after.get('pre_reference_correction_metrics') != before.get('metrics')):
                raise ValueError('Reference correction metrics are not traceable')
            left = {k: v for k, v in before.items() if k not in ('metrics', 'reference_sha256')}
            right = {k: v for k, v in after.items()
                     if k not in ('metrics', 'reference_sha256', 'pre_reference_correction_metrics')}
        else:
            left, right = before, after
        if left != right:
            raise ValueError('Reference correction changed provider evidence')
    if affected != receipt.get('affected_logical_results'):
        raise ValueError('Reference correction affected scope mismatch')
    require_valid_teds_for_release(current)
    return receipt


def validate_webmainbench_scoring_revision(root):
    """Verify the zero-call shared-population correction and provider identity."""
    receipt = json.loads(contained_path(root, 'scoring-correction.json').read_text())
    before_results = contained_path(root, 'pre-scoring-correction-results.jsonl')
    after_results = contained_path(root, 'results.jsonl')
    before_dataset = contained_path(root, 'pre-scoring-correction-dataset.json')
    after_dataset = contained_path(root, 'dataset.json')
    if (receipt.get('schema_version') != 1
            or receipt.get('kind') != 'webmainbench_shared_population_scoring_correction'
            or receipt.get('provider_calls') != 0
            or receipt.get('input_results_sha256') != hashlib.sha256(before_results.read_bytes()).hexdigest()
            or receipt.get('output_results_sha256') != hashlib.sha256(after_results.read_bytes()).hexdigest()
            or receipt.get('input_dataset_sha256') != hashlib.sha256(before_dataset.read_bytes()).hexdigest()
            or receipt.get('output_dataset_sha256') != hashlib.sha256(after_dataset.read_bytes()).hexdigest()):
        raise ValueError('Invalid WebMainBench shared-population correction binding')
    before = load_jsonl(before_results)
    after = load_jsonl(after_results)
    if len(before) != len(after) or len(after) != receipt.get('logical_results'):
        raise ValueError('WebMainBench scoring correction result scope mismatch')
    allowed = {'metrics', 'meta', 'reference_sha256',
               'pre_scoring_correction_metrics'}
    for left, right in zip(before, after):
        if (left['attempt_id'] != right['attempt_id']
                or right.get('pre_scoring_correction_metrics') != left.get('metrics')
                or {key: value for key, value in left.items() if key not in allowed}
                != {key: value for key, value in right.items() if key not in allowed}):
            raise ValueError('WebMainBench scoring correction changed provider evidence')
    expected_denominators = {'text_edit': 545, 'code_edit': 110,
                             'formula_edit': 257, 'table_edit': 179,
                             'table_TEDS': 179}
    providers = receipt.get('providers', [])
    actual_providers = {row['provider'] for row in after}
    if (not providers or len(providers) != len(set(providers))
            or set(providers) != actual_providers):
        raise ValueError('WebMainBench scoring correction provider scope mismatch')
    for provider in providers:
        rows = [row for row in after if row['provider'] == provider]
        if len(rows) != expected_denominators['text_edit']:
            raise ValueError('WebMainBench scoring correction provider matrix is incomplete')
        counts = {name: sum(row['metrics'][name].get('success', False) for row in rows)
                  for name in expected_denominators}
        if counts != expected_denominators:
            raise ValueError('WebMainBench methods do not share reference-defined denominators')
    require_valid_teds_for_release(after)
    return receipt


def validate_teds_revision_if_present(root, records):
    """A revision marker or receipt requires the complete verified chain."""
    if (root / 'teds-correction.json').exists() or any('official_metrics' in row for row in records):
        result = validate_teds_revision(root)
        if (root / 'reference-correction.json').is_file():
            validate_reference_revision(root)
        if (root / 'scoring-correction.json').is_file():
            validate_webmainbench_scoring_revision(root)
        return result
    return None
