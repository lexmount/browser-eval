"""Public CLI runs and rescoring can save data independently of local HTML."""
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'harness/web_fetch/data/fixture-dataset.json'
PROGRAM = '''
import os
import sys
import urllib.request

def no_publication(*args, **kwargs):
    raise AssertionError('Local evaluation/report commands must not publish or load publication credentials')

urllib.request.urlopen = no_publication
from harness.web_fetch import artifact_auth
artifact_auth.load_token = no_publication
from harness.web_fetch import cli
if os.environ.get('TEST_REJECT_REPORT_BUILDER'):
    def reject_builder(*args, **kwargs):
        raise ValueError('renderer failure sentinel')
    cli.build_report = reject_builder
sys.argv = ['browser-harness-eval', *sys.argv[1:]]
cli.main()
'''


def invoke(tmp_path, *arguments, reject_builder=False, language='en'):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('ARTIFACT_SITE_', 'ARTIFACT_HUB_'))}
    env.update(PYTHONPATH=str(ROOT), BROWSER_EVAL_REPORT_LANGUAGE=language,
               TEST_REJECT_REPORT_BUILDER='1' if reject_builder else '')
    return subprocess.run([sys.executable, '-c', PROGRAM, *map(str, arguments)],
                          cwd=tmp_path, env=env, text=True, capture_output=True)


def run_fixture(tmp_path, *, no_report=False, reject_builder=False, fail_on_failure=False, language='en'):
    env_file = tmp_path / 'empty.env'
    env_file.write_text('')
    arguments = ['run', '--delay', '0', '--output', tmp_path / 'run', '--env-file', env_file]
    if no_report:
        arguments.append('--no-report')
    if fail_on_failure:
        arguments.append('--fail-on-failure')
    return invoke(tmp_path, *arguments, reject_builder=reject_builder, language=language)


def saved_records(run):
    for name in ('manifest.json', 'dataset.json', 'summary.json', 'results.jsonl'):
        assert (run / name).is_file()
    rows = [json.loads(line) for line in (run / 'results.jsonl').read_text().splitlines()]
    assert len(rows) == 2
    assert {row['ok'] for row in rows} == {True, False}
    for row in rows:
        assert row['metrics']['content_sha256']
        assert (run / row['artifact_path']).is_file()
    return rows


def test_no_report_saves_complete_scored_data_without_rendering(tmp_path):
    result = run_fixture(tmp_path, no_report=True, reject_builder=True, fail_on_failure=True)
    assert result.returncode == 2, result.stdout + result.stderr
    saved_records(tmp_path / 'run')
    assert not (tmp_path / 'run/report').exists()
    assert str(tmp_path / 'run/results.jsonl') in result.stdout
    assert 'Report:' not in result.stdout
    assert 'not built' in result.stdout


def test_default_run_keeps_local_report_convenience_without_publication(tmp_path):
    result = run_fixture(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = saved_records(tmp_path / 'run')
    assert (tmp_path / 'run/report/index.html').is_file()
    report_rows = [json.loads(line) for line in (tmp_path / 'run/report/results.jsonl').read_text().splitlines()]
    assert report_rows == rows
    assert 'Report:' in result.stdout


def test_saved_data_can_build_a_local_report_later(tmp_path):
    result = run_fixture(tmp_path, no_report=True, reject_builder=True)
    assert result.returncode == 0, result.stdout + result.stderr
    run = tmp_path / 'run'
    before = (run / 'results.jsonl').read_bytes()
    result = invoke(tmp_path, 'report', run)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (run / 'report/index.html').is_file()
    assert (run / 'results.jsonl').read_bytes() == before


def test_rescore_no_report_preserves_existing_html_and_reports_staleness(tmp_path):
    result = run_fixture(tmp_path, no_report=True)
    assert result.returncode == 0, result.stdout + result.stderr
    run = tmp_path / 'run'
    report = run / 'report'
    report.mkdir()
    (report / 'index.html').write_text('Previous report')
    (report / 'report-languages.json').write_text('not valid JSON')
    before = {path.name: path.read_bytes() for path in report.iterdir()}
    result = invoke(tmp_path, 'rescore', run, '--dataset', DATASET, '--no-report',
                    reject_builder=True, language='unsupported-report-language')
    assert result.returncode == 0, result.stdout + result.stderr
    saved_records(run)
    assert {path.name: path.read_bytes() for path in report.iterdir()} == before
    assert 'not rebuilt' in result.stdout
    assert 'report:' not in result.stdout.lower()
    assert str(run / 'results.jsonl') in result.stdout


def test_no_report_does_not_validate_unrequested_report_language(tmp_path):
    result = run_fixture(tmp_path, no_report=True, reject_builder=True, language='unsupported-report-language')
    assert result.returncode == 0, result.stdout + result.stderr
    saved_records(tmp_path / 'run')


def test_default_renderer_errors_are_not_swallowed_after_saving_results(tmp_path):
    result = run_fixture(tmp_path, reject_builder=True)
    assert result.returncode != 0
    assert 'renderer failure sentinel' in result.stderr
    saved_records(tmp_path / 'run')
    assert 'Report:' not in result.stdout


def test_default_rescore_rebuilds_local_report_without_publication(tmp_path):
    result = run_fixture(tmp_path, no_report=True)
    assert result.returncode == 0, result.stdout + result.stderr
    run = tmp_path / 'run'
    result = invoke(tmp_path, 'rescore', run, '--dataset', DATASET)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = saved_records(run)
    assert (run / 'report/index.html').is_file()
    assert [json.loads(line) for line in (run / 'report/results.jsonl').read_text().splitlines()] == rows
    assert 'report:' in result.stdout


def test_no_report_rescore_still_rejects_tampered_evidence(tmp_path):
    result = run_fixture(tmp_path, no_report=True)
    assert result.returncode == 0, result.stdout + result.stderr
    run = tmp_path / 'run'
    rows = saved_records(run)
    before = (run / 'results.jsonl').read_bytes()
    (run / rows[0]['artifact_path']).write_text('Changed after collection')
    result = invoke(tmp_path, 'rescore', run, '--dataset', DATASET, '--no-report', reject_builder=True)
    assert result.returncode != 0
    assert 'Artifact differs from its result binding' in result.stderr
    assert (run / 'results.jsonl').read_bytes() == before
    assert 'Rescored ' not in result.stdout
    assert not (run / 'report').exists()
