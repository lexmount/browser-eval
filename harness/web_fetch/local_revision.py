"""Validate task replacements and offline revisions of a bound local snapshot."""
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


def require_local_task_replacement(source, binding, dataset, records):
    """Verify complete new task calls while preserving every unrelated saved row."""
    receipt = json.loads(read_bound(source, binding).read_text())
    if receipt.get('kind') != 'local_task_replacement' or not receipt.get('reason'):
        raise ValueError('Task replacement needs a declared reason')
    before = json.loads(read_bound(source, receipt['baseline_dataset']).read_text())
    previous = load_jsonl(read_bound(source, receipt['baseline_results']))
    cohort = json.loads(read_bound(source, receipt['cohort_dataset']).read_text())
    calls = load_jsonl(read_bound(source, receipt['cohort_results']))
    removed, added = receipt['removed_case_id'], receipt['added_case_id']
    old_cases = {c['id']: c for c in before['cases']}
    new_cases = {c['id']: c for c in dataset['cases']}
    if (removed == added or removed not in old_cases or added in old_cases
            or len(old_cases) != len(before['cases']) or len(new_cases) != len(dataset['cases'])
            or set(new_cases) != (set(old_cases) - {removed}) | {added}
            or before.get('coverage_plan') != dataset.get('coverage_plan')
            or any(new_cases[k] != v for k, v in old_cases.items() if k != removed)
            or cohort['cases'] != [new_cases[added]]
            or old_cases[removed].get('track') != new_cases[added].get('track')):
        raise ValueError('Task replacement changed unrelated cases or task family')
    providers = set(before['coverage_plan'][old_cases[removed].get('track', 'web')])
    expected = {(p, added) for p in providers}
    if (len(calls) != len(expected) or receipt.get('provider_calls_added') != len(calls)
            or {(r['provider'], r['case_id']) for r in calls} != expected
            or any(r.get('run') != 1 or r.get('provider_metadata', {}).get('retry') for r in calls)):
        raise ValueError('Task replacement needs one new call from every method')
    inherited = [r for r in previous if r['case_id'] != removed]
    rows = {r['attempt_id']: r for r in records}
    wanted = {r['attempt_id']: r for r in inherited + calls}
    if (len(rows) != len(records) or len(wanted) != len(inherited) + len(calls)
            or len(records) != len(previous) or rows != wanted):
        raise ValueError('Task replacement altered an inherited or newly measured result')
    if any(r['metrics'].get('evaluation_excluded') for r in calls):
        raise ValueError('Replacement task cannot be excluded after seeing its results')
    from .local_audit import require_local_execution_contract
    if not receipt.get('execution_contract') or not receipt.get('cohort_execution'):
        raise ValueError('Task replacement requires its complete execution contract')
    contract = json.loads(read_bound(source, receipt['execution_contract']).read_text())
    execution = json.loads(read_bound(source, receipt['cohort_execution']).read_text())
    if not contract.get('runner_inputs'):
        raise ValueError('Task replacement requires retained runner inputs and source observations')
    require_local_execution_contract(source, cohort, calls, contract, execution)
    return {identifier: row['metrics']['scoring_source_sha256']
            for identifier, row in rows.items()}


def replacement_predecessor(source, binding):
    """Follow hash-linked score receipts back to the original replacement view."""
    seen = set()
    while True:
        identity = (binding['path'], binding['sha256'])
        if identity in seen:
            raise ValueError('Local score revision lineage repeats a receipt')
        seen.add(identity)
        receipt = json.loads(read_bound(source, binding).read_text())
        if (receipt.get('kind') != 'local_offline_score_revision'
                or receipt.get('provider_calls_added') != 0):
            raise ValueError('Invalid predecessor score revision')
        for key in ('input_dataset', 'input_results'):
            read_bound(source, receipt[key])
        parent = receipt.get('predecessor_revision')
        if parent is None:
            return receipt['input_dataset'], receipt['input_results']
        prior = json.loads(read_bound(source, parent).read_text())
        if (prior.get('output_dataset_sha256') != receipt['input_dataset']['sha256']
                or prior.get('output_results_sha256') != receipt['input_results']['sha256']):
            raise ValueError('Local score revision predecessor does not match its inputs')
        binding = parent


def require_local_revision(source, binding, dataset, records):
    receipt = json.loads(read_bound(source, binding).read_text())
    replacement_predecessor(source, binding)
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
        if fields - {'task_prompt', 'acceptance_contract', 'body_requirements', 'required_patterns', 'required_facts', 'forbidden_any', 'diagnostic_any', 'acceptance_policy_version'}:
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
                    new.get('scoring', {}).get('rule_revision') is not True):
                raise ValueError('Scoring policy change cannot be recorded as a parser-only correction')
        else:
            expected_scoring[identifier] = old['metrics']['scoring_source_sha256']
            before = {k: v for k, v in old['metrics'].items() if not k.startswith('evaluation_exclu')}
            after = {k: v for k, v in new['metrics'].items() if not k.startswith('evaluation_exclu')}
            if before != after:
                raise ValueError('Unselected score changed: ' + identifier)
        if new['case_id'] in changed_cases and identifier not in scored:
            raise ValueError('Changed case was not rescored across every method')
    if receipt.get('input_verdicts'):
        from .metrics import task_passed
        proof = json.loads(read_bound(source, receipt['input_verdicts']).read_text())
        verdicts = proof.get('verdicts', {})
        if (proof.get('input_results_sha256') != receipt['input_results']['sha256']
                or set(verdicts) != set(old_rows)
                or any(type(value) is not bool for value in verdicts.values())
                or not proof.get('frozen_predicate') or not proof.get('snapshot_manifest')):
            raise ValueError('Frozen input verdict proof is incomplete')
        read_bound(source, proof['frozen_predicate'])
        read_bound(source, proof['snapshot_manifest'])
        transitions = [{'attempt_id': row['attempt_id'], 'before': verdicts[row['attempt_id']],
                        'after': task_passed(row['ok'], row['metrics'])}
                       for row in records if row['attempt_id'] in scored
                       and verdicts[row['attempt_id']] != task_passed(row['ok'], row['metrics'])]
        if transitions != receipt.get('task_verdict_changes'):
            raise ValueError('Local revision verdict transitions differ from frozen input proof')
    from .local_audit import _require_page_identity_review
    _require_page_identity_review(source, dataset, records)
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
    input_results_sha256 = hashlib.sha256(results_bytes).hexdigest()
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
        dataset['comparison'].pop('scoring_policy_version', None)
        prior_review = dataset['comparison'].pop('independent_review', None)
        if prior_review:
            dataset['comparison']['historical_independent_review'] = prior_review
    additional = set(config.get('rescore_attempt_ids', []))
    if not additional <= {row['attempt_id'] for row in records}:
        raise ValueError('Local revision has an invalid additional rescoring scope')
    revision_key = (input_results_sha256[:16] + '-' +
                    hashlib.sha256(configuration.read_bytes()).hexdigest()[:16])
    history = source / 'local-evidence/offline-revisions' / revision_key
    if history.exists():
        raise ValueError('Local revision already exists; preserve the prior receipt')
    indexed = {case['id']: case for case in dataset['cases']}
    input_verdicts = None
    if config.get('input_verdicts'):
        proof = json.loads(read_bound(source, config['input_verdicts']).read_text())
        input_verdicts = proof.get('verdicts', {})
        if (proof.get('input_results_sha256') != input_results_sha256
                or set(input_verdicts) != {row['attempt_id'] for row in records}
                or any(type(value) is not bool for value in input_verdicts.values())):
            raise ValueError('Frozen input verdicts do not match the complete input results')
    selected = []
    before_verdicts = {}
    for row in records:
        if row['case_id'] not in replacements and row['attempt_id'] not in additional:
            continue
        case = indexed[row['case_id']]
        raw = contained_path(source, row['artifact_path'], reject_symlinks=True).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['metrics']['content_sha256']:
            raise ValueError('Local revision output integrity failure')
        before = (input_verdicts[row['attempt_id']] if input_verdicts is not None
                  else task_passed(row['ok'], row['metrics']))
        before_verdicts[row['attempt_id']] = before
        row['metrics'] = score_attempt(case, raw, ok=row['ok'], status_code=row.get('status_code'),
            title=row.get('title'), final_url=row.get('final_url'), metadata=row.get('provider_metadata'),
            previous_metrics=row['metrics'], list_reference=load_list_reference(case, source))
        row['scoring'] = {**row.get('scoring', {}), 'mode': 'offline_rescore',
            'scored_at': datetime.now(timezone.utc).isoformat(),
            'rule_revision': bool(config.get('rule_revision')),
            'input_results_sha256': input_results_sha256}
        selected.append(row['attempt_id'])
    from .local_audit import carry_reviewed_page_identities
    identity_binding = carry_reviewed_page_identities(source, original_dataset, dataset, records)
    changes = [{'attempt_id': row['attempt_id'], 'before': before_verdicts[row['attempt_id']],
                'after': task_passed(row['ok'], row['metrics'])}
               for row in records if row['attempt_id'] in before_verdicts
               and before_verdicts[row['attempt_id']] != task_passed(row['ok'], row['metrics'])]
    apply_cohort_exclusions(source, dataset, records)
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
        'input_verdicts': config.get('input_verdicts'),
        **({'predecessor_revision': config['predecessor_revision']} if config.get('predecessor_revision') else {}),
        'superseded_controls': config.get('superseded_controls', []),
        'page_identity_review': identity_binding,
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
