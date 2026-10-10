"""Derived input commands validate before publishing and preserve source authority."""
import json
from copy import deepcopy

import pytest

from harness.trajectory_replay.public_webarena.qualification import (
    build_authoring_suite,
    build_observation_suite,
)
from harness.trajectory_replay.public_webarena.qualification import (
    main_authoring_suite as authoring_main,
)
from harness.trajectory_replay.public_webarena.qualification import (
    main_observation_suite as observation_main,
)
from tests.public_webarena_fixture import authoring_suite_fixture


def invoke(entry, arguments):
    entry(arguments)


def command_inputs(tmp_path, kind):
    suite, qualification = authoring_suite_fixture()
    source, receipt = tmp_path / 'suite.json', tmp_path / 'qualification.json'
    source.write_text(json.dumps(suite))
    receipt.write_text(json.dumps(qualification))
    output = tmp_path / 'derived/suite.json'
    args = ['--suite', str(source), '--output', str(output)]
    if kind == 'authoring':
        args += ['--qualification', str(receipt)]
        entry = authoring_main
    else:
        args += ['--task-ids', 'webarena-1']
        entry = observation_main
    return source, output, entry, args


@pytest.mark.parametrize('kind', ['authoring', 'observation'])
def test_invalid_derived_suite_never_becomes_a_visible_output(tmp_path, kind):
    source, output, entry, args = command_inputs(tmp_path, kind)
    payload = json.loads(source.read_text())
    del payload['tasks'][0]['title']
    source.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        invoke(entry, args)
    assert not output.exists()


@pytest.mark.parametrize('kind', ['authoring', 'observation'])
def test_command_refuses_to_replace_a_different_derived_suite(tmp_path, kind):
    _, output, entry, args = command_inputs(tmp_path, kind)
    output.parent.mkdir()
    output.write_text('retained evidence\n')
    with pytest.raises(ValueError):
        invoke(entry, args)
    assert output.read_text() == 'retained evidence\n'


@pytest.mark.parametrize('kind', ['authoring', 'observation'])
def test_command_is_repeatable_and_keeps_canonical_inputs_unchanged(tmp_path, kind):
    source, output, entry, args = command_inputs(tmp_path, kind)
    before = source.read_bytes()
    invoke(entry, args)
    result = output.read_bytes()
    invoke(entry, args)
    assert output.read_bytes() == result
    assert source.read_bytes() == before
    payload = json.loads(result)
    assert payload['benchmark']['response_observation_probe']['non_canonical'] is True
    assert payload['tasks'][0]['ground_truth']['qualification']['status'] == 'pending'


@pytest.mark.parametrize('option', ['task_types', 'source_dispositions'])
def test_explicit_empty_authoring_filter_cannot_expand_the_selection(option):
    suite, qualification = authoring_suite_fixture()
    with pytest.raises(ValueError):
        build_authoring_suite(suite, qualification, **{option: set()})


def test_duplicate_qualification_rows_cannot_overwrite_a_disposition():
    suite, qualification = authoring_suite_fixture()
    qualification['tasks'].insert(0, {'task_id': 'webarena-1', 'disposition': 'eligible'})
    with pytest.raises(ValueError):
        build_authoring_suite(suite, qualification)


def test_duplicate_source_tasks_cannot_select_the_last_copy():
    suite, _ = authoring_suite_fixture()
    suite['tasks'].append(deepcopy(suite['tasks'][0]))
    with pytest.raises(ValueError):
        build_observation_suite(suite, ['webarena-1'])


def test_probe_output_cannot_replace_an_existing_trajectory_output():
    suite, _ = authoring_suite_fixture()
    with pytest.raises(ValueError):
        build_observation_suite(suite, ['webarena-1'], output_name='page')


def test_explicit_empty_cli_task_scope_cannot_select_all_tasks(tmp_path):
    _, output, entry, args = command_inputs(tmp_path, 'authoring')
    with pytest.raises(ValueError):
        invoke(entry, [*args, '--task-ids', ''])
    assert not output.exists()


@pytest.mark.parametrize('kind', ['authoring', 'observation'])
def test_command_rejects_output_symlink_without_changing_its_target(tmp_path, kind):
    _, output, entry, args = command_inputs(tmp_path, kind)
    target = tmp_path / 'retained.json'
    target.write_text('retained evidence\n')
    output.parent.mkdir()
    output.symlink_to(target)
    with pytest.raises(ValueError):
        invoke(entry, args)
    assert output.is_symlink()
    assert target.read_text() == 'retained evidence\n'


@pytest.mark.parametrize('timeout', [0, -1, True, 1.5])
def test_authoring_deadline_requires_a_positive_integer(timeout):
    suite, qualification = authoring_suite_fixture()
    with pytest.raises(ValueError, match='timeout must be positive'):
        build_authoring_suite(suite, qualification, target_timeout_ms=timeout)


@pytest.mark.parametrize('name', ['', ' ', None])
def test_observation_output_requires_a_nonempty_name(name):
    suite, _ = authoring_suite_fixture()
    with pytest.raises(ValueError, match='output name'):
        build_observation_suite(suite, ['webarena-1'], output_name=name)
