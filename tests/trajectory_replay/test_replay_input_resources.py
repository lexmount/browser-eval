"""Installed fixture resources and explicit evidence survive relocated HF suites."""
import hashlib
import html
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from harness.trajectory_replay import report_inputs


@pytest.mark.parametrize('family', ['curated', 'human'])
@pytest.mark.parametrize('resolved', [False, True])
def test_runner_serves_packaged_pages_when_suite_is_in_an_unrelated_cache(
    tmp_path, monkeypatch, family, resolved
):
    if family == 'curated':
        from harness.trajectory_replay import runner
    else:
        from harness.trajectory_replay.public_webarena import runner
    if family == 'human':
        monkeypatch.setattr(runner, 'validate_resource_controls', lambda info: None)
        monkeypatch.setattr(runner, 'require_linux_cgroup_v2', lambda client: None)
    from harness.benchmarks import data
    track = 'trajectory-replay' if family == 'curated' else 'webarena-replay'
    definition = ('suites/curated-v1.json' if family == 'curated'
                  else 'suites/human-qualified-v1.json')
    cache = tmp_path / 'unrelated-cache'
    monkeypatch.setenv('BROWSER_EVAL_DATASET_CACHE', str(cache))
    revision = '1' * 40
    suite = cache / track / revision / 'definitions' / definition
    suite.parent.mkdir(parents=True)
    suite.write_text('{}')
    ledger = {'revision': revision, 'files': {
        'definitions/' + definition: {'sha256': hashlib.sha256(suite.read_bytes()).hexdigest()},
    }}
    monkeypatch.setattr(data, 'binding', lambda track, revision=None: ledger)
    monkeypatch.setattr(data, 'definition_path', lambda *args: suite)
    # Unbound cache neighbors cannot shadow the packaged locked-input fixtures.
    (suite.parent/'fixtures').mkdir()
    (suite.parent/'fixtures/core.html').write_text('Unbound cache page')
    task = SimpleNamespace(task_id='local', environment={}, ground_truth={},
                           tool_calls=({'url': '{fixture_origin}/core.html'},))
    monkeypatch.setattr(runner, 'load_suite', lambda path: SimpleNamespace(tasks=(task,)))
    monkeypatch.setattr(runner.docker, 'from_env', MagicMock())
    monkeypatch.setattr(runner, 'sync_playwright', MagicMock())

    class LocatedFixture(Exception):
        pass

    def start(root):
        assert root != suite.parent / 'fixtures'
        assert (root / 'core.html').is_file()
        assert (root / 'forms.html').is_file()
        assert (root / 'page2.html').is_file()
        assert 'html' in (root / 'core.html').read_text().lower()
        raise LocatedFixture

    monkeypatch.setattr(runner, '_start_fixture_server', start)
    with pytest.raises(LocatedFixture):
        runner.run_evaluation(suite_path=suite if resolved else None, output_dir=tmp_path/'run',
                              reports_dir=tmp_path/'reports')


def test_measured_smoke_is_explicit_and_hash_bound(tmp_path):
    (tmp_path / 'agent_smoke.json').write_text('{"results": [{"status": "pass"}]}')
    assert report_inputs.agent_smoke_input(None) is None
    evidence = tmp_path / 'evidence.json'
    raw = b'{"results": [{"status": "fail"}], "executed_at":"2026-09-01"}'
    evidence.write_bytes(raw)
    actual = report_inputs.agent_smoke_input(evidence)
    assert actual['results'] == [{'status': 'fail'}]
    assert actual['provenance']['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert actual['provenance']['reused_not_rerun'] is True
    with pytest.raises(FileNotFoundError):
        report_inputs.agent_smoke_input(tmp_path / 'missing.json')
    evidence.write_text('{}')
    with pytest.raises(ValueError, match='results'):
        report_inputs.agent_smoke_input(evidence)


@pytest.mark.parametrize('family', ['curated', 'human'])
def test_explicit_missing_smoke_fails_before_browser_start(tmp_path, monkeypatch, family):
    if family == 'curated':
        from harness.trajectory_replay import runner
    else:
        from harness.trajectory_replay.public_webarena import runner
    suite = tmp_path / 'suite.json'
    suite.write_text('{}')
    monkeypatch.setattr(runner, 'load_suite', lambda path: SimpleNamespace(tasks=()))
    docker = MagicMock()
    monkeypatch.setattr(runner.docker, 'from_env', docker)
    with pytest.raises(FileNotFoundError):
        runner.run_evaluation(suite_path=suite, output_dir=tmp_path/'run',
                              reports_dir=tmp_path/'reports',
                              agent_smoke_path=tmp_path/'missing-smoke.json')
    docker.assert_not_called()


@pytest.mark.parametrize('definition,edition', [
    ('suites/human-qualified-v1.json', 'current'),
    ('inventories/human-candidates-v1.json', 'current'),
    ('inventories/human-candidates-v1.json', 'historical'),
])
def test_reproduction_command_selects_the_actual_suite_revision(
    tmp_path, monkeypatch, definition, edition
):
    from harness.benchmarks import data
    from harness.trajectory_replay.public_webarena import report
    digest = 'e' * 64
    old = {'revision': '1' * 40, 'files': {}}
    current = {'revision': '2' * 40, 'files': {}, 'history': [old]}
    selected = current if edition == 'current' else old
    selected['files']['definitions/' + definition] = {'sha256': digest}
    # A newer qualified edition must never replace the historical candidate input.
    monkeypatch.setattr(data, 'binding', lambda track: current)
    changelog = tmp_path / 'changes.json'
    changelog.write_text(json.dumps({'schema': 'lexmount_webarena_trajectory_report_changes/1',
                                    'releases': [{'date': '2026-09-01', 'type': 'initial',
                                                  'effect': 'Published'}]}))
    monkeypatch.setattr(report, '_report_input', lambda name: changelog)
    rendered = html.unescape(report._appendix_page({'suite_sha256': digest}))
    assert '--suite "$(uv run --frozen python -c ' in rendered
    assert definition in rendered
    assert selected['revision'] in rendered
    assert 'eligible 的 30 个任务' not in rendered
    assert digest in rendered
    assert report_inputs.replay_suite_binding(digest, family='human') == {
        'benchmark_dataset': 'webarena-replay', 'definition': definition,
        'revision': selected['revision'],
    }


def test_custom_reproduction_requires_explicit_suite_not_current_default(tmp_path, monkeypatch):
    from harness.benchmarks import data
    from harness.trajectory_replay.public_webarena import report
    monkeypatch.setattr(data, 'binding', lambda track: {'files': {}, 'revision': '1' * 40})
    changelog = tmp_path / 'changes.json'
    changelog.write_text(json.dumps({'schema': 'lexmount_webarena_trajectory_report_changes/1',
                                    'releases': [{'date': 'today', 'type': 'initial',
                                                  'effect': 'Published'}]}))
    monkeypatch.setattr(report, '_report_input', lambda name: changelog)
    rendered = html.unescape(report._appendix_page({'suite_sha256': 'f'*64}))
    assert '--suite <frozen-suite.json>' in rendered
    assert 'f'*64 in rendered


def test_report_preserves_recorded_edition_when_a_newer_release_has_identical_suite(
    tmp_path, monkeypatch
):
    from harness.benchmarks import data
    from harness.trajectory_replay.public_webarena import report
    digest = 'e' * 64
    definition = 'suites/human-qualified-v1.json'
    files = {'definitions/' + definition: {'sha256': digest}}
    old = {'revision': '1' * 40, 'files': files}
    current = {'revision': '2' * 40, 'files': files, 'history': [old]}
    monkeypatch.setattr(data, 'binding', lambda track, revision=None:
                        old if revision == old['revision'] else current)
    changelog = tmp_path / 'changes.json'
    changelog.write_text(json.dumps({
        'schema': 'lexmount_webarena_trajectory_report_changes/1',
        'releases': [{'date': 'today', 'type': 'initial', 'effect': 'Published'}],
    }))
    monkeypatch.setattr(report, '_report_input', lambda name: changelog)
    manifest = {'suite_sha256': digest, 'suite_dataset': {
        'benchmark_dataset': 'webarena-replay', 'definition': definition,
        'revision': old['revision'],
    }}
    rendered = html.unescape(report._appendix_page(manifest))
    assert old['revision'] in rendered
    assert current['revision'] not in rendered
    manifest['suite_dataset']['definition'] = 'inventories/human-candidates-v1.json'
    with pytest.raises(ValueError, match='binding differs'):
        report._appendix_page(manifest)


@pytest.mark.parametrize('family', ['curated', 'human'])
def test_cli_forwards_explicit_smoke_input(tmp_path, monkeypatch, family):
    import sys
    if family == 'curated':
        from harness.trajectory_replay import cli
    else:
        from harness.trajectory_replay.public_webarena import cli
        monkeypatch.setattr(cli, 'load_runtime_env', lambda path: None)
        monkeypatch.setattr(cli, 'configure_browser_docker_host', lambda path: None)
    run = MagicMock()
    monkeypatch.setattr(cli, 'run_evaluation', run)
    smoke = str(tmp_path / 'chosen.json')
    monkeypatch.setattr(sys, 'argv', ['replay', '--suite', str(tmp_path/'suite.json'),
                                     '--ground-truth-evidence', str(tmp_path/'truth.json'),
                                     '--agent-smoke', smoke, '--output', str(tmp_path/'run'),
                                     '--no-report'])
    cli.main()
    assert run.call_args.kwargs['agent_smoke_path'] == smoke


@pytest.mark.parametrize('family', ['curated', 'human'])
def test_explicit_suite_uses_its_custom_fixture_tree(tmp_path, monkeypatch, family):
    if family == 'curated':
        from harness.trajectory_replay import runner
    else:
        from harness.trajectory_replay.public_webarena import runner
        monkeypatch.setattr(runner, 'validate_resource_controls', lambda info: None)
        monkeypatch.setattr(runner, 'require_linux_cgroup_v2', lambda client: None)
    suite = tmp_path/'historical'/'suite.json'
    suite.parent.mkdir()
    suite.write_text('{}')
    fixture = suite.parent/'fixtures'
    (fixture/'nested').mkdir(parents=True)
    (fixture/'core.html').write_text('<h1>Original historical core</h1>')
    (fixture/'nested/custom.html').write_text('<h1>Original custom page</h1>')
    (fixture/'nested/site.css').write_text('h1 {color:blue}')
    task = SimpleNamespace(task_id='local', environment={}, ground_truth={},
                           tool_calls=({'url': '{fixture_origin}/nested/custom.html'},))
    monkeypatch.setattr(runner, 'load_suite', lambda path: SimpleNamespace(tasks=(task,)))
    monkeypatch.setattr(runner.docker, 'from_env', MagicMock())
    monkeypatch.setattr(runner, 'sync_playwright', MagicMock())

    class CorrectHistoricalFiles(Exception):
        pass

    def start(root):
        assert root == fixture
        assert (root/'core.html').read_text() == '<h1>Original historical core</h1>'
        assert (root/'nested/custom.html').is_file()
        raise CorrectHistoricalFiles

    monkeypatch.setattr(runner, '_start_fixture_server', start)
    with pytest.raises(CorrectHistoricalFiles):
        runner.run_evaluation(suite_path=suite, output_dir=tmp_path/'run',
                              reports_dir=tmp_path/'reports')


@pytest.mark.parametrize('family', ['curated', 'human'])
@pytest.mark.parametrize('failure', ['missing-directory', 'missing-page', 'symlink'])
def test_invalid_explicit_fixtures_fail_before_engine_initialization(
    tmp_path, monkeypatch, family, failure
):
    if family == 'curated':
        from harness.trajectory_replay import runner
    else:
        from harness.trajectory_replay.public_webarena import runner
    suite = tmp_path/'suite.json'
    suite.write_text('{}')
    root = tmp_path/'fixtures'
    if failure != 'missing-directory':
        root.mkdir()
        if failure == 'symlink':
            outside = tmp_path/'outside.html'
            outside.write_text('Unbound outside page')
            (root/'custom.html').symlink_to(outside)
        else:
            (root/'other.html').write_text('Wrong page')
    task = SimpleNamespace(task_id='local', environment={}, ground_truth={},
                           tool_calls=({'url': '{fixture_origin}/custom.html'},))
    monkeypatch.setattr(runner, 'load_suite', lambda path: SimpleNamespace(tasks=(task,)))
    docker, browser = MagicMock(), MagicMock()
    monkeypatch.setattr(runner.docker, 'from_env', docker)
    monkeypatch.setattr(runner, 'sync_playwright', browser)
    with pytest.raises(ValueError, match='fixture|symlink'):
        runner.run_evaluation(suite_path=suite, output_dir=tmp_path/'run',
                              reports_dir=tmp_path/'reports')
    docker.assert_not_called()
    browser.assert_not_called()


def test_fixture_hashes_include_nested_custom_names_and_non_html_assets(tmp_path):
    root = tmp_path/'fixtures'
    (root/'nested').mkdir(parents=True)
    payloads = {'core.html': b'Historical core', 'nested/custom.html': b'Custom page',
                'nested/data.json': b'{"version":1}', 'nested/site.css': b'body {color:red}'}
    for relative, raw in payloads.items():
        (root/relative).write_bytes(raw)
    assert report_inputs.replay_fixture_hashes(root) == {
        relative: hashlib.sha256(raw).hexdigest() for relative, raw in payloads.items()
    }
