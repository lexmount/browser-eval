"""Conserve task judgments across explicit offline corrections and exclusions."""
from collections import Counter


VERDICTS = {'pass', 'fail', 'excluded', 'unavailable'}


def summarize_state(state):
    if not isinstance(state, dict) or not state or any(value not in VERDICTS for value in state.values()):
        raise ValueError('Invalid result ledger state')
    counts = Counter(state.values())
    return {key: counts[key] for key in ('pass', 'fail', 'excluded', 'unavailable')}


def build_ledger(states):
    """Derive every total and transition from per-attempt states, never prose."""
    if not states:
        raise ValueError('Result ledger requires a baseline')
    inventory = set(states[0]['verdicts'])
    rounds = []
    previous = None
    for state in states:
        if (not state.get('id') or not state.get('source_results_sha256')
                or set(state['verdicts']) != inventory):
            raise ValueError('Result ledger inventory or source binding differs')
        totals = summarize_state(state['verdicts'])
        changes = [] if previous is None else [
            {'attempt_id': identifier, 'before': previous[identifier], 'after': verdict}
            for identifier, verdict in sorted(state['verdicts'].items())
            if previous[identifier] != verdict]
        added = sum(row['after'] == 'pass' and row['before'] != 'pass' for row in changes)
        removed = sum(row['before'] == 'pass' and row['after'] != 'pass' for row in changes)
        before_passes = totals['pass'] if previous is None else summarize_state(previous)['pass']
        if before_passes + added - removed != totals['pass']:
            raise ValueError('Result ledger pass totals do not conserve')
        rounds.append({'id': state['id'], 'source_results_sha256': state['source_results_sha256'],
                       'before_passes': before_passes, 'added_passes': added,
                       'removed_passes': removed, 'totals': totals, 'changes': changes})
        previous = state['verdicts']
    return {'schema_version': 1, 'logical_results': len(inventory), 'states': states,
            'rounds': rounds, 'current': rounds[-1]['totals']}


def validate_ledger(ledger, records=None):
    if ledger.get('schema_version') != 1:
        raise ValueError('Unsupported result ledger')
    rebuilt = build_ledger(ledger['states'])
    if ledger != rebuilt:
        raise ValueError('Result ledger counts or transitions differ from its states')
    if records is not None:
        from .metrics import task_measured, task_passed
        actual = {record['attempt_id']: (
            'excluded' if record['metrics'].get('evaluation_excluded') else
            'unavailable' if not task_measured(record['metrics']) else
            'pass' if task_passed(record['ok'], record['metrics']) else 'fail') for record in records}
        if len(actual) != len(records) or actual != ledger['states'][-1]['verdicts']:
            raise ValueError('Result ledger does not match current scored results')
    return rebuilt
