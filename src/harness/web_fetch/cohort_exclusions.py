"""Exclude a proven evaluation defect symmetrically without discarding calls."""
import hashlib

from lexbench_webfetch.paths import contained_path


def require_cohort_exclusions(source, dataset, records):
    declared = dataset.get('comparison', {}).get('excluded_cases', [])
    cases = {case['id'] for case in dataset['cases']}
    indexed = {}
    for item in declared:
        case_id = item.get('case_id')
        if case_id not in cases or case_id in indexed or not item.get('reason', '').strip():
            raise ValueError('Invalid or duplicate cohort exclusion')
        proof = contained_path(source, item['evidence_path'], reject_symlinks=True)
        if hashlib.sha256(proof.read_bytes()).hexdigest() != item.get('evidence_sha256'):
            raise ValueError('Cohort exclusion evidence changed')
        indexed[case_id] = item
    for record in records:
        metrics = record['metrics']
        declaration = indexed.get(record['case_id'])
        if bool(metrics.get('evaluation_excluded')) != bool(declaration):
            raise ValueError('Cohort exclusion must apply to every method for the same case')
        if declaration and metrics.get('evaluation_exclusion_reason') != declaration['reason']:
            raise ValueError('Cohort exclusion reason differs from its evidence binding')
    return indexed


def apply_cohort_exclusions(source, dataset, records):
    """Annotate derived metrics only; acceptance and physical call facts remain saved."""
    declared = {item['case_id']: item for item in dataset.get('comparison', {}).get('excluded_cases', [])}
    for record in records:
        metrics = record['metrics']
        metrics.pop('evaluation_excluded', None)
        metrics.pop('evaluation_exclusion_reason', None)
        if item := declared.get(record['case_id']):
            metrics.update(evaluation_excluded=True, evaluation_exclusion_reason=item['reason'])
    require_cohort_exclusions(source, dataset, records)
