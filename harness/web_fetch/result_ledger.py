"""Conserve task judgments across explicit offline corrections and exclusions."""
from collections import Counter


VERDICTS = {'pass', 'fail', 'excluded', 'unavailable'}


def summarize_state(state):
    if not isinstance(state, dict) or not state or any(value not in VERDICTS for value in state.values()):
        raise ValueError('Invalid result ledger state')
    counts = Counter(state.values())
    return {key: counts[key] for key in ('pass', 'fail', 'excluded', 'unavailable')}


def build_ledger(states, *, predecessor=None):
    """Derive every total and transition from per-attempt states, never prose."""
    if not states:
        raise ValueError('Result ledger requires a baseline')
    inventory = set(states[0]['verdicts'])
    rounds = []
    previous = None
    for state in states:
        current_inventory = set(state['verdicts'])
        replacement = state.get('replacement')
        if not state.get('id') or not state.get('source_results_sha256'):
            raise ValueError('Result ledger inventory or source binding differs')
        if current_inventory != inventory:
            removed, added = inventory - current_inventory, current_inventory - inventory
            if (previous is None or not replacement
                    or set(replacement.get('removed_attempt_ids', [])) != removed
                    or set(replacement.get('added_attempt_ids', [])) != added
                    or not removed or len(removed) != len(added)
                    or {key.split('__')[0] for key in removed} != {key.split('__')[0] for key in added}
                    or any(previous[key] != state['verdicts'][key]
                           for key in inventory & current_inventory)):
                raise ValueError('Result ledger replacement is not an atomic complete-method transition')
        elif replacement:
            raise ValueError('Result ledger replacement must change the declared inventory')
        totals = summarize_state(state['verdicts'])
        changes = [] if previous is None else [
            {'attempt_id': identifier, 'before': previous.get(identifier), 'after': state['verdicts'].get(identifier)}
            for identifier in sorted(set(previous) | current_inventory)
            if previous.get(identifier) != state['verdicts'].get(identifier)]
        added = sum(row['after'] == 'pass' and row['before'] != 'pass' for row in changes)
        removed = sum(row['before'] == 'pass' and row['after'] != 'pass' for row in changes)
        before_passes = totals['pass'] if previous is None else summarize_state(previous)['pass']
        if before_passes + added - removed != totals['pass']:
            raise ValueError('Result ledger pass totals do not conserve')
        rounds.append({'id': state['id'], 'source_results_sha256': state['source_results_sha256'],
                       'before_passes': before_passes, 'added_passes': added,
                       'removed_passes': removed, 'totals': totals, 'changes': changes})
        previous = state['verdicts']
        inventory = current_inventory
    result = {'schema_version': 2 if predecessor else 1, 'logical_results': len(inventory),
              'states': states, 'rounds': rounds, 'current': rounds[-1]['totals']}
    if predecessor:
        result['predecessor'] = predecessor
    return result


def validate_ledger(ledger, records=None, *, source=None):
    if ledger.get('schema_version') not in {1, 2}:
        raise ValueError('Unsupported result ledger')
    rebuilt = build_ledger(ledger['states'], predecessor=ledger.get('predecessor'))
    if ledger != rebuilt:
        raise ValueError('Result ledger counts or transitions differ from its states')
    if (any(state.get('replacement') for state in ledger['states'])
            and not ledger.get('predecessor')):
        raise ValueError('Task replacement requires bound predecessor evidence')
    if ledger.get('predecessor'):
        if source is None:
            raise ValueError('Result ledger needs its bound predecessor evidence')
        import json
        from .local_revision import read_bound, require_local_task_replacement
        from harness.record_io import load_jsonl
        prior = json.loads(read_bound(source, ledger['predecessor']).read_text())
        validate_ledger(prior, source=source)
        count = len(prior['states'])
        if ledger['states'][:count] != prior['states'] or len(ledger['states']) <= count:
            raise ValueError('Result ledger does not retain the complete predecessor chain')
        for previous, state in zip(ledger['states'], ledger['states'][1:]):
            event = state.get('replacement')
            if not event:
                continue
            receipt = json.loads(read_bound(source, event['receipt']).read_text())
            if receipt['baseline_results']['sha256'] != previous['source_results_sha256']:
                raise ValueError('Task replacement does not continue the predecessor results')
            dataset = json.loads(read_bound(source, event['output_dataset']).read_text())
            output = load_jsonl(read_bound(source, event['output_results']))
            require_local_task_replacement(source, event['receipt'], dataset, output)
            proof = json.loads(read_bound(source, event['output_verdicts']).read_text())
            if (proof['input_results_sha256'] != state['source_results_sha256']
                    or event['output_results']['sha256'] != state['source_results_sha256']
                    or set(proof['verdicts']) != set(state['verdicts'])
                    or any(type(value) is not bool for value in proof['verdicts'].values())
                    or {key: 'pass' if value else 'fail' for key, value in proof['verdicts'].items()}
                       != state['verdicts']):
                raise ValueError('Task replacement verdicts differ from frozen output evidence')
            read_bound(source, proof['frozen_predicate'])
            read_bound(source, proof['snapshot_manifest'])
            before = load_jsonl(read_bound(source, receipt['baseline_results']))
            calls = load_jsonl(read_bound(source, receipt['cohort_results']))
            removed = {row['attempt_id'] for row in before if row['case_id'] == receipt['removed_case_id']}
            added = {row['attempt_id'] for row in calls}
            if (set(event['removed_attempt_ids']) != removed
                    or set(event['added_attempt_ids']) != added):
                raise ValueError('Task replacement inventory differs from its physical calls')
    if records is not None:
        from lexbench_webfetch.metrics import task_measured, task_passed
        actual = {record['attempt_id']: (
            'excluded' if record['metrics'].get('evaluation_excluded') else
            'unavailable' if not task_measured(record['metrics']) else
            'pass' if task_passed(record['ok'], record['metrics']) else 'fail') for record in records}
        if len(actual) != len(records) or actual != ledger['states'][-1]['verdicts']:
            raise ValueError('Result ledger does not match current scored results')
    return rebuilt
