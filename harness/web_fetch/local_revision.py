"""Validate offline score revisions of a hash-bound local diagnostic snapshot."""
import hashlib
import json

from harness.record_io import load_jsonl

from .cohort_exclusions import require_cohort_exclusions
from .safety import contained_path
from .scoring_regressions import require_scoring_regressions


def read_bound(source, binding):
    path = contained_path(source, binding['path'], reject_symlinks=True)
    if hashlib.sha256(path.read_bytes()).hexdigest() != binding.get('sha256'):
        raise ValueError('Local revision evidence binding changed: ' + binding['path'])
    return path


def require_local_revision(source, binding, dataset, records):
    receipt = json.loads(read_bound(source, binding).read_text())
    if receipt.get('kind') != 'local_offline_score_revision' or receipt.get('provider_calls_added') != 0:
        raise ValueError('Local revision must preserve original provider calls')
    before_dataset = json.loads(read_bound(source, receipt['input_dataset']).read_text())
    previous = load_jsonl(read_bound(source, receipt['input_results']))
    for name, key in [('dataset.json', 'output_dataset_sha256'), ('results.jsonl', 'output_results_sha256')]:
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != receipt.get(key):
            raise ValueError('Local revision output binding changed: ' + name)
    regressions = receipt.get('regression_ids')
    if not regressions:
        raise ValueError('Local revision requires executable regression provenance')
    require_scoring_regressions(regressions)
    old_cases = {case['id']: case for case in before_dataset['cases']}
    new_cases = {case['id']: case for case in dataset['cases']}
    if set(old_cases) != set(new_cases) or before_dataset.get('coverage_plan') != dataset.get('coverage_plan'):
        raise ValueError('Local revision changed the original case/method matrix')
    changed_cases = set()
    for identifier, old in old_cases.items():
        new = new_cases[identifier]
        fields = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
        if fields - {'task_prompt', 'acceptance_contract', 'body_requirements', 'required_patterns', 'required_facts', 'acceptance_policy_version'}:
            raise ValueError('Local score revision changed source or acquisition inputs')
        if fields:
            changed_cases.add(identifier)
    if changed_cases != set(receipt.get('changed_case_ids', [])):
        raise ValueError('Local revision case scope differs')
    if changed_cases and receipt.get('rule_revision') is not True:
        raise ValueError('Changed acceptance contract requires an explicit rule revision')
    old_rows = {row['attempt_id']: row for row in previous}
    new_rows = {row['attempt_id']: row for row in records}
    if len(old_rows) != len(previous) or len(new_rows) != len(records) or set(old_rows) != set(new_rows):
        raise ValueError('Local revision changed the call inventory')
    scored = set(receipt.get('rescored_attempt_ids', []))
    if not scored <= set(new_rows):
        raise ValueError('Local revision rescoring scope differs')
    expected_scoring = {}
    for identifier, old in old_rows.items():
        new = new_rows[identifier]
        if ({k: v for k, v in old.items() if k not in {'metrics', 'scoring', 'diagnosis', 'failure_category'}}
                != {k: v for k, v in new.items() if k not in {'metrics', 'scoring', 'diagnosis', 'failure_category'}}
                or old['metrics']['content_sha256'] != new['metrics']['content_sha256']):
            raise ValueError('Local revision changed measured call facts: ' + identifier)
        if identifier in scored:
            expected_scoring[identifier] = receipt['scoring_source_sha256']
            if (receipt.get('rule_revision') and
                    (receipt.get('acceptance_policy_version') != 2
                     or new['metrics'].get('acceptance_policy_version') != 2
                     or new.get('scoring', {}).get('rule_revision') is not True)):
                raise ValueError('Scoring policy change cannot be recorded as a parser-only correction')
        else:
            expected_scoring[identifier] = old['metrics']['scoring_source_sha256']
            before = {k: v for k, v in old['metrics'].items() if not k.startswith('evaluation_exclu')}
            after = {k: v for k, v in new['metrics'].items() if not k.startswith('evaluation_exclu')}
            if before != after:
                raise ValueError('Unselected score changed: ' + identifier)
        if new['case_id'] in changed_cases and identifier not in scored:
            raise ValueError('Changed case was not rescored across every method')
    require_cohort_exclusions(source, dataset, records)
    return expected_scoring


def revise_local_snapshot(source, configuration):
    """Apply declared score-only changes in a separate snapshot, retaining predecessors."""
    import copy
    from datetime import datetime, timezone
    from .scoring import score_attempt, load_list_reference, SCORING_SOURCE_SHA256
    from .cohort_exclusions import apply_cohort_exclusions
    from .metrics import task_passed

    config = json.loads(configuration.read_text())
    regressions = config.get('regression_ids', [])
    require_scoring_regressions(regressions)
    if not regressions or not config.get('authority', {}).get('description'):
        raise ValueError('Local revision requires authority and regression provenance')
    dataset_bytes = (source / 'dataset.json').read_bytes()
    results_bytes = (source / 'results.jsonl').read_bytes()
    original_dataset = json.loads(dataset_bytes)
    dataset = copy.deepcopy(original_dataset)
    records = load_jsonl(source / 'results.jsonl')
    replacements = {case['id']: case for case in config['cases']}
    if len(replacements) != len(config['cases']) or not replacements.keys() <= {c['id'] for c in dataset['cases']}:
        raise ValueError('Local revision has an invalid case scope')
    dataset['cases'] = [replacements.get(case['id'], case) for case in dataset['cases']]
    if dataset['cases'] != original_dataset['cases'] and config.get('rule_revision') is not True:
        raise ValueError('Changed acceptance contract requires an explicit rule revision')
    dataset['comparison']['excluded_cases'] = config.get('excluded_cases', [])
    if config.get('rule_revision'):
        dataset['comparison']['scoring_policy_version'] = 2
    additional = set(config.get('rescore_attempt_ids', []))
    if not additional <= {row['attempt_id'] for row in records}:
        raise ValueError('Local revision has an invalid additional rescoring scope')
    indexed = {case['id']: case for case in dataset['cases']}
    selected = []
    changes = []
    for row in records:
        if row['case_id'] not in replacements and row['attempt_id'] not in additional:
            continue
        case = indexed[row['case_id']]
        raw = contained_path(source, row['artifact_path'], reject_symlinks=True).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['metrics']['content_sha256']:
            raise ValueError('Local revision output integrity failure')
        before = task_passed(row['ok'], row['metrics'])
        row['metrics'] = score_attempt(case, raw, ok=row['ok'], status_code=row.get('status_code'),
            title=row.get('title'), final_url=row.get('final_url'), metadata=row.get('provider_metadata'),
            previous_metrics=row['metrics'], list_reference=load_list_reference(case, source))
        row['scoring'] = {**row.get('scoring', {}), 'mode': 'offline_rescore',
            'scored_at': datetime.now(timezone.utc).isoformat(),
            'rule_revision': bool(config.get('rule_revision')),
            'acceptance_policy_version': row['metrics']['acceptance_policy_version'],
            'input_results_sha256': hashlib.sha256(results_bytes).hexdigest()}
        selected.append(row['attempt_id'])
        after = task_passed(row['ok'], row['metrics'])
        if before != after:
            changes.append({'attempt_id': row['attempt_id'], 'before': before, 'after': after})
    apply_cohort_exclusions(source, dataset, records)
    history = source / 'local-evidence/offline-revisions' / hashlib.sha256(results_bytes).hexdigest()[:16]
    history.mkdir(parents=True, exist_ok=False)
    (history / 'dataset-before.json').write_bytes(dataset_bytes)
    (history / 'results-before.jsonl').write_bytes(results_bytes)
    def binding(path):
        return {'path': str(path.relative_to(source)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    new_dataset = (json.dumps(dataset, ensure_ascii=False, indent=2) + '\n').encode()
    new_results = ''.join(json.dumps(row, ensure_ascii=True) + '\n' for row in records).encode()
    receipt = {'kind': 'local_offline_score_revision', 'provider_calls_added': 0,
        'input_dataset': binding(history / 'dataset-before.json'),
        'input_results': binding(history / 'results-before.jsonl'),
        'output_dataset_sha256': hashlib.sha256(new_dataset).hexdigest(),
        'output_results_sha256': hashlib.sha256(new_results).hexdigest(),
        'scoring_source_sha256': SCORING_SOURCE_SHA256,
        'changed_case_ids': sorted(case['id'] for case in dataset['cases']
            if case != next(c for c in original_dataset['cases'] if c['id'] == case['id'])),
        'rescored_attempt_ids': selected, 'rule_revision': bool(config.get('rule_revision')),
        'acceptance_policy_version': 2,
        'task_verdict_changes': changes, 'regression_ids': regressions, 'authority': config['authority']}
    receipt_path = history / 'receipt.json'
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    if (source / 'dataset.json').read_bytes() != dataset_bytes or (source / 'results.jsonl').read_bytes() != results_bytes:
        raise ValueError('Local revision source changed during scoring')
    (source / 'dataset.json').write_bytes(new_dataset)
    (source / 'results.jsonl').write_bytes(new_results)
    try:
        require_local_revision(source, binding(receipt_path), dataset, records)
    except BaseException:
        (source / 'dataset.json').write_bytes(dataset_bytes)
        (source / 'results.jsonl').write_bytes(results_bytes)
        raise
    return receipt
