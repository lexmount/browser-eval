"""Exercise cohort orchestration with synthetic browser and reset boundaries."""

import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from harness.trajectory_replay import remote_execution as execution
from harness.trajectory_replay.remote_runtime import file_sha256, selected_task_ids
from tests.trajectory_replay.test_remote_three_method_cohort import _fixture


def environment(tmp_path, monkeypatch, *, subset=None, reset_drift=False, call_drift=False,
                broken_evidence=False, task_error=False):
    args, manifest, _ = _fixture(tmp_path, monkeypatch, task_ids=[1, 2, 3])
    args.tasks = subset
    authority = tmp_path / 'authority'
    correction_path = (
        authority / 'evaluation/trajectory-replay/webarena-verified/task-corrections.json'
    )
    correction_path.parent.mkdir(parents=True)
    correction_path.write_text('{}')
    command = authority / 'scripts/run_remote_three_method_cohort.py'
    command.parent.mkdir()
    command.write_text('# synthetic command entry\n')
    monkeypatch.setattr(execution, 'ROOT', authority)
    monkeypatch.setattr(execution, 'load_corrections', lambda: SimpleNamespace(records={}))
    monkeypatch.setattr(execution, 'prepare_browser_viewport', lambda _: None)
    activity = {'resets': 0, 'starts': 0, 'terminated': 0, 'waited': 0, 'tasks': []}

    def reset(*_):
        activity['resets'] += 1
        if reset_drift:
            args.manifest.write_text(json.dumps({**manifest, 'changed_after_validation': True}))
        return 'reset-receipt-hash'

    monkeypatch.setattr(execution, 'reset_cohort', reset)

    class Process:
        def __init__(self, *_args, **_kwargs):
            activity['starts'] += 1

        def terminate(self):
            activity['terminated'] += 1

        def wait(self, **_kwargs):
            activity['waited'] += 1
            return 0

    monkeypatch.setattr(execution.subprocess, 'Popen', Process)
    monkeypatch.setattr(execution, '_wait_cdp', lambda *_: None)
    page = SimpleNamespace(
        url='https://admin.example/admin',
        goto=lambda *_a, **_kw: SimpleNamespace(status=200),
        locator=lambda *_: SimpleNamespace(evaluate_all=lambda *_: ['https://admin.example/a.js']),
        evaluate=lambda *_: 'function',
    )
    service = SimpleNamespace(_page=page, _submit=lambda callback: callback())
    module = ModuleType('agent.tools.browser.browser_tool')
    module.BrowserTool = lambda **_: SimpleNamespace(
        _get_service=lambda: service, close=lambda: None
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)

    def task(**kwargs):
        task_id = kwargs['task_id']
        activity['tasks'].append(task_id)
        if task_error:
            raise RuntimeError('synthetic task interrupted')
        folder = args.output / f'task-{task_id}'
        folder.mkdir()
        hashes = {}
        for name, filename in (
            ('trace', 'trace.json'), ('readiness', 'readiness.json'),
            ('network_har', 'network.har'), ('actual_response', 'actual_response.json'),
            ('evaluation', 'evaluation.json'),
        ):
            path = folder / filename
            path.write_text('{}')
            hashes[name] = file_sha256(path)
        if broken_evidence:
            (folder / 'actual_response.json').write_text('{"changed": true}')
        if call_drift:
            args.binary.write_bytes(b'changed browser after calls started')
        return {
            'task_id': task_id,
            'executed_actions_sha256': manifest['tasks'][task_id - 1]['bound_calls_sha256'],
            'status': 'fail', 'score': 0.0, 'error': '', 'evidence_sha256': hashes,
        }

    monkeypatch.setattr(execution, '_run_task', task)
    return args, activity


@pytest.mark.parametrize('subset, task_ids, promotable', [
    (None, [1, 2, 3], True), ('1,3', [1, 3], False),
])
def test_complete_calls_are_certified_even_when_tasks_fail(tmp_path, monkeypatch, subset,
                                                         task_ids, promotable):
    args, activity = environment(tmp_path, monkeypatch, subset=subset)
    old_path = list(sys.path)
    mask = os.umask(0o022)
    try:
        assert execution.run_cohort(args) == 0
        observed_mask = os.umask(0o022)
        assert observed_mask == 0o022
    finally:
        os.umask(mask)
    assert sys.path == old_path
    receipt = json.loads((args.output / 'receipt.json').read_text())
    assert receipt['certified'] is True and receipt['promotable'] is promotable
    assert receipt['declared_task_ids'] == [1, 2, 3]
    assert receipt['executed_task_ids'] == task_ids == activity['tasks']
    assert all(row['score'] == 0.0 for row in receipt['results'])
    assert receipt['reference_sha256_before'] == receipt['reference_sha256_after']
    assert receipt['reference_sha256_before']['runner'] == file_sha256(Path(execution.__file__))
    assert activity['resets'] == activity['starts'] == 1
    assert activity['terminated'] == activity['waited'] == 1


def test_site_reset_cannot_change_previously_validated_inputs(tmp_path, monkeypatch):
    args, activity = environment(tmp_path, monkeypatch, reset_drift=True)
    with pytest.raises(ValueError, match='inputs changed during preflight or site reset'):
        execution.run_cohort(args)
    assert activity['resets'] == 1 and activity['starts'] == 0
    assert not (args.output / 'receipt.json').exists()


def test_changes_during_calls_keep_evidence_but_prevent_certification(tmp_path, monkeypatch):
    args, activity = environment(tmp_path, monkeypatch, call_drift=True)
    assert execution.run_cohort(args) == 1
    receipt = json.loads((args.output / 'receipt.json').read_text())
    assert receipt['certified'] is receipt['promotable'] is False
    assert receipt['reference_sha256_before'] != receipt['reference_sha256_after']
    assert activity['tasks'] == [1, 2, 3]
    assert (args.output / 'task-3/actual_response.json').exists()


@pytest.mark.parametrize('fault', ['broken_evidence', 'task_error'])
def test_failure_closes_browser_and_cannot_install_receipt(tmp_path, monkeypatch, fault):
    args, activity = environment(tmp_path, monkeypatch, **{fault: True})
    old_path = list(sys.path)
    with pytest.raises((ValueError, RuntimeError)):
        execution.run_cohort(args)
    assert activity['terminated'] == activity['waited'] == 1
    assert sys.path == old_path
    assert not (args.output / 'receipt.json').exists()


def test_invalid_subset_is_rejected_before_reset_or_browser_start(tmp_path, monkeypatch):
    args, activity = environment(tmp_path, monkeypatch, subset='1,1')
    with pytest.raises(ValueError, match='subset must be unique'):
        execution.run_cohort(args)
    assert activity['resets'] == activity['starts'] == 0
    assert not args.output.exists()


@pytest.mark.parametrize('rows', [[], [{'task_id': True}], [{'task_id': '1'}],
                                  [{'task_id': 1}, {'task_id': 1}], [{'task_id': 1}, None]])
def test_malformed_selection_is_rejected_without_coercing_task_ids(tmp_path, rows):
    path = tmp_path / 'selection.json'
    path.write_text(json.dumps({'schema': 'lex_webarena_verified_pilot/1', 'tasks': rows}))
    with pytest.raises(ValueError, match='selected-task set'):
        selected_task_ids(path)


def test_empty_cohort_cannot_be_certified():
    assert not execution.certify_cohort(manifest={'task_ids': [], 'tasks': []}, rows=[],
                                       before={}, after={}, evidence_valid=True)
