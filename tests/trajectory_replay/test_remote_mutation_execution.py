"""Exercise mutation proposal and execution boundaries with synthetic BrowserTool responses."""

import hashlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay import remote_execution as execution
from harness.trajectory_replay import webarena_plan as plan

TASK_ID = 731


def inputs(tmp_path):
    source = tmp_path / 'source'
    completion = source / plan.COMPLETION_CANDIDATES
    completion.parent.mkdir(parents=True)
    original = (Path(__file__).resolve().parents[2] / plan.COMPLETION_CANDIDATES)
    payload = json.loads(original.read_text())
    rule = next(row for row in payload['rules'] if row['task_id'] == 465)
    rule.update({'task_id': TASK_ID, 'action_count': 1})
    rule.pop('pre_action_readiness', None)
    payload['rules'] = [rule]
    completion.write_text(json.dumps(payload))
    calls = [{'action': 'click', 'selector': '#save'}]
    seeds = tmp_path / 'seeds.json'
    seeds.write_text(json.dumps({'tasks': [{
        'task_id': TASK_ID, 'replay_status': 'ready', 'canonical_candidate_id': 'one',
        'candidates': [{'id': 'one', 'tool_calls': calls}],
    }]}))
    dataset = completion.parent / 'trajectories.json'
    dataset.write_text(json.dumps({
        'provenance': {'seed_manifest_sha256': hashlib.sha256(seeds.read_bytes()).hexdigest()},
        'trajectories': [{'task_id': TASK_ID, 'tool_calls': calls}],
    }))
    proposal = tmp_path / 'proposal.json'
    base = ['--task', str(TASK_ID), '--seeds', str(seeds), '--source-root', str(source)]
    return base, proposal


def retry_policy():
    return {'after_frozen_call_count': 1, 'retry_only_final_read_only_observer': True,
            'retryable_error_exact': plan.NAVIGATION_CONTEXT_LOSS, 'max_observer_attempts': 2,
            'original_action_retries': 0, 'navigation_ready_timeout_ms': 500}


def proposal(tmp_path, *, retry=False):
    base, path = inputs(tmp_path)
    assert execution.main_mutation_diagnostic([*base, '--emit-proposal', str(path)]) == 0
    if retry:
        payload = json.loads(path.read_text())
        payload['observer_retry_policy'] = retry_policy()
        path.write_text(json.dumps(payload))
    return base, path


def environment(tmp_path, monkeypatch, *, score=1, navigation_error=False, trigger_error=False,
                observer_error=False, drift=False, delete_input=False, setup_error=False,
                close_error=False):
    base, path = proposal(tmp_path, retry=True)
    sites = tmp_path / 'sites.json'
    sites.write_text(json.dumps({'schema': 'lex_webarena_remote_sites/1', 'sites': {
        'shopping': 'https://shop.example.test', 'shopping_admin': 'https://admin.example.test/admin',
        'reddit': 'https://reddit.example.test', 'gitlab': 'https://gitlab.example.test',
    }}))
    binary = tmp_path / 'browser'
    binary.write_bytes(b'fixed binary')
    reset = tmp_path / 'reset.json'
    reset.write_text(json.dumps({'task_id': TASK_ID}))
    activity = {'created': 0, 'closed': 0, 'trigger': 0, 'observer': 0,
                'waits': 0, 'evaluations': 0}
    evaluation = SimpleNamespace(score=score, status='PASS' if score else 'FAIL',
                                 model_dump=lambda **_: {'score': score})

    def evaluate(**kwargs):
        assert kwargs['task_id'] == TASK_ID
        assert Path(kwargs['network_trace']).is_file()
        assert kwargs['agent_response']['status'] == 'SUCCESS'
        activity['evaluations'] += 1
        return evaluation

    task = SimpleNamespace(is_mutate_task=True, sites={WebArenaSite.SHOPPING}, start_urls=['start'],
                           eval=[SimpleNamespace(model_dump=lambda **_: {
                               'evaluator': 'NetworkEventEvaluator'})])
    benchmark = SimpleNamespace(get_task=lambda _: task, evaluate_task=evaluate,
                                config=SimpleNamespace(render_url=lambda *_: ['https://shop.example.test']))
    monkeypatch.setattr(execution, 'remote_benchmark', lambda _: benchmark)
    monkeypatch.setattr(execution, 'validate_remote_reset_receipt',
                        lambda *_a, **_kw: {'task_id': TASK_ID})
    monkeypatch.setattr(execution, '_clear_browser_state', lambda *_: None)
    monkeypatch.setattr(execution, 'prepare_browser_viewport',
                        lambda _: {'width': 1280, 'height': 720})
    monkeypatch.setattr(execution, 'login_context', lambda *_: [])

    def wait(*_a, **_kw):
        activity['waits'] += 1

    def screenshot(**kwargs):
        Path(kwargs['path']).write_bytes(b'synthetic frame')

    service = SimpleNamespace(
        _context=object(), _submit=lambda callback: callback(),
        _page=SimpleNamespace(wait_for_load_state=wait, screenshot=screenshot),
    )

    class Tool:
        def __init__(self, **_):
            activity['created'] += 1
            if setup_error:
                raise RuntimeError('synthetic browser setup failed')

        def _get_service(self):
            return service

        def close(self):
            activity['closed'] += 1
            if close_error:
                raise RuntimeError('synthetic browser cleanup failed')

    module = ModuleType('agent.tools.browser.browser_tool')
    module.BrowserTool = Tool
    monkeypatch.setitem(sys.modules, module.__name__, module)

    class Recorder:
        errors = []

        def start(self, *_):
            pass

        def stop(self, _, target):
            target.write_text('{"log":{"entries":[]}}')

    monkeypatch.setattr(execution, 'CdpHarRecorder', Recorder)

    def call(_tool, arguments):
        if arguments['action'] == 'click':
            activity['trigger'] += 1
            return ('error', 'trigger failed') if trigger_error else ('success', 'saved')
        activity['observer'] += 1
        if drift:
            binary.write_bytes(b'changed binary')
        if delete_input:
            binary.unlink()
        if navigation_error and activity['observer'] == 1:
            return 'error', plan.NAVIGATION_CONTEXT_LOSS
        if observer_error:
            return 'error', 'observer returned a different terminal error'
        return 'success', json.dumps({'completion': 'persisted_wishlist_item_observed'})

    monkeypatch.setattr(execution, '_execute_replay_call', call)
    output = tmp_path / 'output'
    argv = [*base, '--proposal', str(path), '--site-map', str(sites), '--reset-receipt', str(reset),
            '--binary', str(binary), '--cdp', 'http://127.0.0.1:12345',
            '--cowagent-repo', str(tmp_path / 'cowagent'), '--output', str(output)]
    return argv, path, output, activity


def test_proposal_round_trip_needs_no_browser_and_retains_canonical_calls(tmp_path, monkeypatch):
    base, path = proposal(tmp_path)
    payload = json.loads(path.read_text())
    assert payload['tool_calls'][0] == {'action': 'click', 'selector': '#save'}
    assert len(payload['tool_calls']) == 2
    monkeypatch.chdir(tmp_path)
    checked = execution.main_mutation_diagnostic(
        [*base, '--proposal', str(path), '--check-proposal']
    )
    assert checked == 0
    payload['tool_calls'][0]['selector'] = '#different'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='preserve the canonical actions'):
        execution.main_mutation_diagnostic([*base, '--proposal', str(path), '--check-proposal'])


@pytest.mark.parametrize('overrides', [
    {'max_observer_attempts': True}, {'max_observer_attempts': 4}, {'original_action_retries': 1},
    {'navigation_ready_timeout_ms': 0}, {'navigation_ready_timeout_ms': True},
    {'navigation_ready_timeout_ms': 30001}, {'after_frozen_call_count': 2},
    {'retryable_error_exact': 'any error'}, {'extra': True},
])
def test_invalid_observer_retry_is_rejected_during_proposal_check(tmp_path, overrides):
    base, path = proposal(tmp_path, retry=True)
    payload = json.loads(path.read_text())
    payload['observer_retry_policy'].update(overrides)
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='bounded compiled read-only observer'):
        execution.main_mutation_diagnostic([*base, '--proposal', str(path), '--check-proposal'])


def test_uncompiled_manual_observation_cannot_claim_read_only_retries():
    with pytest.raises(ValueError, match='compiled read-only observer'):
        plan.mutation_observer_retry_policy(retry_policy(), 1, None)
    assert plan.mutation_observer_retry_policy(None, 1, None) == {}


@pytest.mark.parametrize('navigation_error', [False, True])
def test_diagnostic_retries_only_final_observer_and_closes_tool(
    tmp_path, monkeypatch, navigation_error
):
    argv, _, output, activity = environment(
        tmp_path, monkeypatch, navigation_error=navigation_error
    )
    old_path = list(sys.path)
    mask = os.umask(0o022)
    try:
        assert execution.main_mutation_diagnostic(argv) == 0
        assert os.umask(0o022) == 0o022
    finally:
        os.umask(mask)
    assert sys.path == old_path
    receipt = json.loads((output / 'receipt.json').read_text())
    assert receipt['status'] == 'diagnostic_pass' and receipt['promotable'] is False
    assert receipt['recorded_agent_response_used'] is False
    assert receipt['input_hashes_stable'] is True
    assert activity == {'created': 1, 'closed': 1, 'trigger': 1,
                        'observer': 2 if navigation_error else 1,
                        'waits': 1 if navigation_error else 0, 'evaluations': 1}
    assert receipt['completed_action_count'] == 2
    for name, digest in receipt['evidence'].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('fault, expected', [('trigger_error', 'diagnostic_incomplete'),
                                            ('observer_error', 'diagnostic_incomplete'),
                                            ('drift', 'diagnostic_incomplete'),
                                            ('delete_input', 'diagnostic_incomplete'),
                                            ('close_error', 'diagnostic_incomplete'),
                                            ('score', 'diagnostic_fail')])
def test_failure_keeps_evidence_and_never_repeats_mutation(tmp_path, monkeypatch, fault, expected):
    kwargs = {fault: 0 if fault == 'score' else True}
    argv, _, output, activity = environment(tmp_path, monkeypatch, **kwargs)
    assert execution.main_mutation_diagnostic(argv) == 1
    receipt = json.loads((output / 'receipt.json').read_text())
    assert receipt['status'] == expected and receipt['promotable'] is False
    assert activity['created'] == activity['closed'] == activity['trigger'] == 1
    assert activity['observer'] <= 1 and activity['waits'] == 0
    assert (output / 'trace.json').is_file()


def test_invalid_retry_cannot_create_output_or_browser(tmp_path, monkeypatch):
    argv, path, output, activity = environment(tmp_path, monkeypatch)
    payload = json.loads(path.read_text())
    payload['observer_retry_policy']['navigation_ready_timeout_ms'] = 0
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='bounded compiled read-only observer'):
        execution.main_mutation_diagnostic(argv)
    assert not output.exists() and activity['created'] == 0


def test_missing_binary_is_rejected_before_output_and_tool_creation(tmp_path, monkeypatch):
    argv, _, output, activity = environment(tmp_path, monkeypatch)
    Path(argv[argv.index('--binary') + 1]).unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        execution.main_mutation_diagnostic(argv)
    assert activity['created'] == 0 and not output.exists()


def test_proposal_emission_rejects_dangling_output_link(tmp_path):
    base, path = inputs(tmp_path)
    target = tmp_path / 'unrelated.json'
    path.symlink_to(target)
    with pytest.raises(ValueError, match='proposal output'):
        execution.main_mutation_diagnostic([*base, '--emit-proposal', str(path)])
    assert not target.exists()


def test_setup_failure_retains_terminal_receipt_without_actions(tmp_path, monkeypatch):
    argv, _, output, activity = environment(tmp_path, monkeypatch, setup_error=True)
    old_path = list(sys.path)
    assert execution.main_mutation_diagnostic(argv) == 1
    assert sys.path == old_path
    receipt = json.loads((output / 'receipt.json').read_text())
    assert receipt['status'] == 'diagnostic_incomplete' and receipt['completed_action_count'] == 0
    assert 'synthetic browser setup failed' in receipt['error']
    assert activity['created'] == 1 and activity['trigger'] == activity['observer'] == 0


def test_preflight_cannot_change_frozen_inputs_before_browser_start(tmp_path, monkeypatch):
    argv, _, output, activity = environment(tmp_path, monkeypatch)
    binary = Path(argv[argv.index('--binary') + 1])

    def reset_receipt(*_a, **_kw):
        binary.write_bytes(b'changed during preflight')
        return {'task_id': TASK_ID}

    monkeypatch.setattr(execution, 'validate_remote_reset_receipt', reset_receipt)
    with pytest.raises(ValueError, match='inputs changed during diagnostic preflight'):
        execution.main_mutation_diagnostic(argv)
    assert not output.exists() and activity['created'] == 0
