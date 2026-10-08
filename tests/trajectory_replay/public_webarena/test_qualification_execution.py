"""Exercise qualification planning and resumable execution without browser calls."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

from harness.trajectory_replay.public_webarena import qualification


def inputs(tmp_path):
    suite = tmp_path / 'suite.json'
    suite.write_text(json.dumps({'schema': 'lex_browser_use_tasks/4', 'tasks': [{
        'id': 'a', 'title': 'Save', 'difficulty': 'simple',
        'trajectory': {'schema': 'lex_browser_trajectory/1',
                       'actions': [{'action': 'click', 'selector': '#save'}]},
        'ground_truth': {'expected_actions': ['click'], 'qualification': {'status': 'pending'}},
        'source': {'url': 'https://example.test/task'},
        'evaluator': {'kind': 'webarena_verified', 'task_id': 1, 'revision': 1},
        'environment': {'kind': 'webarena_verified', 'sites': ['shopping']},
    }]}))
    scope = tmp_path / 'scope.json'
    scope.write_text(json.dumps({
        'schema': 'lex_browser_use_native_scope/1',
        'suite_sha256': hashlib.sha256(suite.read_bytes()).hexdigest(),
        'included_task_ids': ['a'],
        'qualification_batches': [{'id': 'shopping', 'sites': ['shopping'],
                                   'shard_count': 1, 'task_ids': ['a']}],
    }))
    config = tmp_path / 'config.json'
    config.write_text('{}')
    evidence = tmp_path / 'evidence'
    argv = ['--suite', str(suite), '--native-scope', str(scope), '--webarena-config',
            str(config), '--evidence-root', str(evidence)]
    return suite, scope, evidence, argv


def test_dry_run_never_relocates_incomplete_previous_evidence(tmp_path, monkeypatch):
    _, _, evidence, argv = inputs(tmp_path)
    previous = evidence / 'pass-1/shopping-0-of-1'
    previous.mkdir(parents=True)
    (previous / 'partial.json').write_bytes(b'original failed evidence')
    monkeypatch.setattr(sys, 'argv', ['qualification', *argv, '--dry-run'])
    qualification.main_qualification()
    assert (previous / 'partial.json').read_bytes() == b'original failed evidence'
    assert not (evidence / '_failed').exists()
    assert not (evidence / 'pass-2').exists()


@pytest.mark.parametrize('batch_id', ['../escape', '/absolute', 'a/b', 'a\\b'])
def test_shard_planning_rejects_batch_ids_that_escape_output(tmp_path, batch_id):
    suite, scope, _, _ = inputs(tmp_path)
    data = json.loads(scope.read_text())
    data['qualification_batches'][0]['id'] = batch_id
    scope.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='batch.*identifier'):
        qualification.qualification_shards(scope, suite)


def write_shard(command, *, check, env):
    """Synthetic child output using the public qualification artifact contract."""
    assert check is True and isinstance(env, dict)
    arguments = iter(command[3:])
    flags = {key: next(arguments) for key in arguments if key != '--qualification-run'}
    output = Path(flags['--output'])
    output.mkdir(parents=True)
    evidence = output / 'tasks/a/chromium/attempt-1'
    evidence.mkdir(parents=True)
    (evidence / 'network.har').write_text(json.dumps({
        'log': {'creator': {'name': 'browser-eval-cdp', 'version': '3'}, 'entries': []},
    }))
    task = qualification.load_suite(Path(flags['--suite'])).tasks[0]
    task_payload = {
        'id': task.task_id, 'title': task.title, 'trajectory': task.trajectory,
        'evaluator': task.evaluator, 'environment': task.environment,
        'ground_truth': task.ground_truth, 'source': task.source,
    }
    pass_index = int(flags['--qualification-pass-index'])
    row = {
        'task_id': 'a', 'engine': 'chromium', 'qualification_run': True,
        'qualification_pass_index': pass_index, 'qualification_required_passes': 2,
        'is_retry': False, 'attempt': 1, 'run_id': f'pass-{pass_index}',
        'process_status': 'fail', 'result_status': 'fail', 'combined_status': 'fail',
        'trace': [], 'evidence_directory': str(evidence),
        'task_contract_sha256': qualification.task_contract_sha256(task_payload),
    }
    (output / 'attempts.jsonl').write_text(json.dumps(row) + '\n')
    (output / 'summary.json').write_text('{}')
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    selection = {
        'kind': 'native_scope_batch', 'scope_sha256': digest(Path(flags['--native-scope'])),
        'batch_id': 'shopping', 'shard_count': 1, 'shard_index': 0,
        'qualification_pass_index': pass_index, 'qualification_required_passes': 2,
    }
    (output / 'manifest.json').write_text(json.dumps({
        'suite_sha256': digest(Path(flags['--suite'])),
        'artifact_provenance': {'attempts_sha256': digest(output / 'attempts.jsonl'),
                                'summary_sha256': digest(output / 'summary.json')},
        'protocol': {'qualification_run': True, 'engines': ['chromium'],
                     'max_failure_retries': 0, 'task_filter': ['a'], 'task_selection': selection,
                     'qualification_pass_index': pass_index, 'qualification_required_passes': 2},
    }))
    return output


def test_two_pass_execution_preserves_incomplete_output_and_resumes(tmp_path, monkeypatch):
    _, _, evidence, argv = inputs(tmp_path)
    previous = evidence / 'pass-1/shopping-0-of-1'
    previous.mkdir(parents=True)
    (previous / 'partial.txt').write_bytes(b'original partial attempt')
    calls = []

    def child(command, **kwargs):
        calls.append(write_shard(command, **kwargs))
        (tmp_path / 'config.json').write_text(json.dumps({'runtime_generation': len(calls)}))

    monkeypatch.setattr(qualification.subprocess, 'run', child)
    qualification.main_qualification(argv)
    assert len(calls) == 2
    preserved = list((evidence / '_failed').glob('*/partial.txt'))
    assert len(preserved) == 1 and preserved[0].read_bytes() == b'original partial attempt'
    queue = evidence / 'qualification-queue.json'
    first_queue = queue.read_bytes()
    parsed = json.loads(first_queue)
    assert parsed['native_scope']['included_count'] == 1
    assert parsed['tasks'][0]['disposition'] == 'needs_trace_repair'
    qualification.main_qualification(argv)
    assert len(calls) == 2 and queue.read_bytes() == first_queue
    assert len(list((evidence / '_failed').iterdir())) == 1


@pytest.mark.parametrize('fault', ['missing-output', 'input-drift'])
def test_invalid_child_execution_cannot_write_final_queue(tmp_path, monkeypatch, fault):
    suite, _, evidence, argv = inputs(tmp_path)

    def child(command, **kwargs):
        if fault == 'input-drift':
            write_shard(command, **kwargs)
            suite.write_text('{"changed": true}')

    monkeypatch.setattr(qualification.subprocess, 'run', child)
    with pytest.raises(ValueError, match='incomplete qualification|inputs changed'):
        qualification.main_qualification(argv)
    assert not (evidence / 'qualification-queue.json').exists()
    assert not (evidence / 'pass-2').exists()


@pytest.mark.parametrize('link_name', ['pass-1', '_failed'])
def test_output_and_quarantine_links_cannot_move_external_evidence(
    tmp_path, monkeypatch, link_name
):
    _, _, evidence, argv = inputs(tmp_path)
    evidence.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep.txt').write_bytes(b'keep')
    (evidence / link_name).symlink_to(outside, target_is_directory=True)
    if link_name == '_failed':
        (evidence / 'pass-1/shopping-0-of-1').mkdir(parents=True)
    monkeypatch.setattr(
        qualification.subprocess, 'run', lambda *_a, **_kw: pytest.fail('child ran')
    )
    with pytest.raises(ValueError, match='Symlinked evidence'):
        qualification.main_qualification(argv)
    assert list(outside.iterdir()) == [outside / 'keep.txt']
    assert (outside / 'keep.txt').read_bytes() == b'keep'


def test_immutable_queue_rejects_link_even_when_content_matches(tmp_path):
    source = tmp_path / 'source.json'
    qualification._write_immutable(source, {'value': 1})
    output = tmp_path / 'queue.json'
    output.symlink_to(source)
    with pytest.raises(ValueError, match='symlinked'):
        qualification._write_immutable(output, {'value': 1})


@pytest.mark.parametrize('kind', ['subset', 'requalification'])
def test_derived_suite_commands_preserve_existing_outputs_and_validate_before_write(
    tmp_path, kind
):
    suite, scope, _, _ = inputs(tmp_path)
    output = tmp_path / 'derived.json'
    if kind == 'subset':
        queue = tmp_path / 'queue.json'
        queue.write_text(json.dumps({'tasks': [{'task_id': 'a', 'disposition': 'eligible'}]}))
        data = json.loads(suite.read_text())
        data['tasks'][0]['tags'] = ['mutate']
        suite.write_text(json.dumps(data))
        main = qualification.main_subset
        argv = ['--suite', str(suite), '--qualification', str(queue), '--dispositions',
                'eligible', '--task-types', 'mutate', '--output', str(output)]
    else:
        main = qualification.main_requalification_suite
        argv = ['--suite', str(suite), '--native-scope', str(scope), '--output', str(output)]
    main(argv)
    original = output.read_bytes()
    main(argv)
    assert output.read_bytes() == original
    data = json.loads(suite.read_text())
    data['tasks'][0]['title'] = 'Changed task'
    suite.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='refusing to overwrite'):
        main(argv)
    assert output.read_bytes() == original
    output.unlink()
    data['tasks'][0]['ground_truth']['expected_actions'] = []
    suite.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='action sequence'):
        main(argv)
    assert not output.exists()


@pytest.mark.parametrize('artifact', ['summary.json', 'tasks/a/chromium/attempt-1/network.har'])
def test_resume_validator_rejects_linked_artifacts_even_when_bytes_match(
    tmp_path, monkeypatch, artifact
):
    suite, scope, evidence, argv = inputs(tmp_path)
    monkeypatch.setattr(qualification.subprocess, 'run', write_shard)
    qualification.main_qualification(argv)
    output = evidence / 'pass-1/shopping-0-of-1'
    path = output / artifact
    target = output / 'saved-original.json'
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match='Symlinked evidence'):
        qualification.validate_completed_shard(
            output, suite_sha256=hashlib.sha256(suite.read_bytes()).hexdigest(),
            scope_sha256=hashlib.sha256(scope.read_bytes()).hexdigest(),
            shard={'id': 'shopping-0-of-1', 'batch_id': 'shopping', 'shard_count': 1,
                   'shard_index': 0, 'task_ids': ['a']}, qualification_pass_index=1,
        )
