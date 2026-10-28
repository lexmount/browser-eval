"""Public diagnostic CLI selects tasks before calls and verifies returned bytes."""
import hashlib
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def experiment(tmp_path):
    png = b'\x89PNG\r\n\x1a\n' + b'fixture bytes' * 8
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            body = b'not a PNG' if self.path == '/invalid' else png
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_port}'
    cases = [{'id': key, 'name': key, 'category': 'synthetic', 'expected_any': [],
              'url': url + '/' + key, 'task_kind': kind, 'track': 'web',
              'content_score_applicable': kind != 'file',
              'source_verification': {'mime': 'image/png', 'file_sha256': digest}}
             for key, kind, digest in [
                 ('good', 'file', hashlib.sha256(png).hexdigest()),
                 ('wrong', 'file', '0' * 64),
                 ('invalid', 'file', hashlib.sha256(b'not a PNG').hexdigest()),
                 ('body', 'body', '')]]
    dataset = tmp_path / 'dataset.json'
    dataset.write_text(json.dumps({'id': 'parent', 'name': 'Parent', 'tool': 'web_fetch',
        'cases': cases, 'coverage_plan': {'web': ['historical-browser']},
        'comparison': {'kind': 'risk_control_100_moli',
                       'require_independent_result_review': True}}))
    config = tmp_path / 'providers.json'
    config.write_text(json.dumps({'providers': {'bytes': {'type': 'http'},
                                              'unused': {'type': 'fixture'}}}))
    env = tmp_path / 'empty.env'
    env.write_text('')

    def run(*arguments):
        return subprocess.run([sys.executable, '-m', 'harness.web_fetch', 'run',
            '--dataset', str(dataset), '--provider-config', str(config),
            '--env-file', str(env), '--output', str(tmp_path / 'run'), '--delay', '0',
            *arguments], cwd=Path(__file__).resolve().parents[1],
            text=True, capture_output=True)

    try:
        yield run, paths, tmp_path, png
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_file_scope_checks_bytes_and_declares_its_actual_matrix(experiment):
    run, paths, root, png = experiment
    original_dataset = (root / 'dataset.json').read_bytes()
    result = run('--providers', 'bytes', '--task-kind', 'file', '--fail-on-failure')
    assert result.returncode == 2, result.stdout + result.stderr
    assert paths == ['/good', '/wrong', '/wrong', '/invalid', '/invalid']
    rows = [json.loads(line) for line in (root / 'run/results.jsonl').read_text().splitlines()]
    assert {row['case_id']: row['metrics']['raw_file_pass'] for row in rows} == {
        'good': True, 'wrong': False, 'invalid': False}
    assert all(row['ok'] for row in rows)
    evidence = json.loads((root / 'run/dataset.json').read_text())
    assert evidence['coverage_plan'] == {'web': ['bytes']}
    assert evidence['comparison']['publication_state'] == 'diagnostic_not_for_release'
    assert 'require_independent_result_review' not in evidence['comparison']
    assert (root / 'dataset.json').read_bytes() == original_dataset
    reliability = json.loads((root / 'run/report/reliability.json').read_text())
    assert reliability['complete'] and reliability['expected_calls'] == 3
    for row in rows[:2]:
        binary = row['provider_metadata']['file_evaluation']['files'][0]
        assert (root / 'run' / binary['artifact_path']).read_bytes() == png
        assert (root / 'run/report' / binary['artifact_path']).read_bytes() == png
    for route in ['index.html', 'attempts/bytes__wrong__r001.html']:
        assert '诊断运行 · 非正式评测' in (root / 'run/report' / route).read_text()
    home = (root / 'run/report/index.html').read_text()
    assert '文件交付' in home and '文件取得率' in home
    assert '常见网页的正文提取' not in home
    detail = (root / 'run/report/attempts/bytes__wrong__r001.html').read_text()
    assert '文件校验未通过' in detail
    from harness.reporting.links import check
    check(root / 'run/report')


def test_case_ids_and_kind_intersect_before_calls(experiment):
    run, paths, root, _ = experiment
    result = run('--providers', 'bytes', '--task-kind', 'file', '--case-ids', 'good,body')
    assert result.returncode == 0, result.stdout + result.stderr
    assert paths == ['/good']
    assert len(json.loads((root / 'run/dataset.json').read_text())['cases']) == 1


@pytest.mark.parametrize('arguments', [
    ['--providers', 'bytes', '--case-ids', 'missing'],
    ['--providers', 'bytes', '--case-ids', 'body', '--task-kind', 'file'],
    ['--providers', 'bytes', '--case-ids', 'good,good'],
    ['--providers', 'bytes,bytes'],
    ['--providers', ''],
])
def test_invalid_scope_never_calls_or_creates_evidence(experiment, arguments):
    run, paths, root, _ = experiment
    result = run(*arguments)
    assert result.returncode != 0
    assert paths == []
    assert not (root / 'run').exists()


def test_existing_evidence_is_never_overwritten(experiment):
    run, paths, root, _ = experiment
    (root / 'run').mkdir()
    existing = root / 'run/results.jsonl'
    existing.write_bytes(b'prior measurements\n')
    result = run('--providers', 'bytes', '--task-kind', 'file')
    assert result.returncode != 0
    assert 'existing evidence is immutable' in result.stderr
    assert paths == []
    assert existing.read_bytes() == b'prior measurements\n'


@pytest.mark.parametrize('rescore', [False, True])
def test_diagnostic_run_rescore_keeps_scope_and_cannot_release(experiment, rescore):
    from harness.web_fetch import assembly, report_package, report_publish
    from harness.web_fetch.reference_lifecycle import require_certifiable_evidence

    run, paths, root, _ = experiment
    original = json.loads((root / 'dataset.json').read_text())
    original['comparison'] = {}
    (root / 'dataset.json').write_text(json.dumps(original))
    result = run('--providers', 'bytes', '--case-ids', 'good')
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = root / 'run'
    before = json.loads((evidence / 'dataset.json').read_text())
    calls = list(paths)
    if rescore:
        result = subprocess.run([sys.executable, '-m', 'harness.web_fetch', 'rescore',
            str(evidence), '--dataset', str(root / 'dataset.json')],
            text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
    after = json.loads((evidence / 'dataset.json').read_text())
    assert after['comparison'] == before['comparison']
    assert after['coverage_plan'] == before['coverage_plan']
    assert [case['id'] for case in after['cases']] == ['good']
    assert paths == calls
    assert '诊断运行 · 非正式评测' in (evidence / 'report/index.html').read_text()
    manifest = json.loads((evidence / 'manifest.json').read_text())
    for dataset in (after, {}):
        with pytest.raises(ValueError, match='Diagnostic'):
            require_certifiable_evidence(dataset, [manifest])
    certificates = root / 'certificates'
    certificates.mkdir()
    with pytest.raises(ValueError, match='Diagnostic'):
        assembly.assemble(evidence, certificates, [], root / 'assembled')
    assert not (root / 'assembled').exists()
    with pytest.raises(ValueError, match='Diagnostic'):
        report_package.build(evidence / 'report', root / 'release.zip')
    assert not (root / 'release.zip').exists()
    config = report_publish.PublishConfig(root / 'profile.json', root / 'release.zip',
        evidence / 'report', root / 'receipt.json', root / 'journal.json', 'create')
    remote = []
    with pytest.raises(ValueError, match='Diagnostic'):
        report_publish.publish(config, transport=lambda *args, **kwargs: remote.append(args))
    assert remote == []


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'url', 'track', 'task_kind'])
def test_diagnostic_rescore_rejects_changed_execution_scope_before_writes(experiment, damage):
    run, paths, root, _ = experiment
    result = run('--providers', 'bytes', '--case-ids', 'good')
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = root / 'run'
    before = {name: (evidence / name).read_bytes()
              for name in ('dataset.json', 'manifest.json', 'results.jsonl', 'summary.json')}
    dataset_path = root / 'dataset.json'
    dataset = json.loads(dataset_path.read_text())
    good = next(case for case in dataset['cases'] if case['id'] == 'good')
    if damage == 'missing':
        dataset['cases'].remove(good)
    elif damage == 'duplicate':
        dataset['cases'].append(dict(good))
    else:
        good[damage] = {'url': 'https://different.invalid/', 'track': 'another',
                        'task_kind': 'body'}[damage]
    dataset_path.write_text(json.dumps(dataset))
    calls = list(paths)
    result = subprocess.run([sys.executable, '-m', 'harness.web_fetch', 'rescore',
        str(evidence), '--dataset', str(dataset_path)], text=True, capture_output=True)
    assert result.returncode != 0
    assert paths == calls
    for name, raw in before.items():
        assert (evidence / name).read_bytes() == raw


@pytest.mark.parametrize("source", ["process", "file"])
def test_invalid_language_rejected_before_provider_construction_or_execution(tmp_path, monkeypatch, source):
    from harness.web_fetch import cli

    def unexpected(*args, **kwargs):
        pytest.fail("An invalid language must fail before preparing or calling a provider")

    monkeypatch.setattr(cli, "build_provider", unexpected)
    monkeypatch.setattr(cli, "run_evaluation", unexpected)
    # Track the original state even when load_runtime_env writes the key directly.
    monkeypatch.setenv("BROWSER_EVAL_REPORT_LANGUAGE", "")
    monkeypatch.delenv("BROWSER_EVAL_REPORT_LANGUAGE")
    env = tmp_path / "local.env"
    env.write_text("BROWSER_EVAL_REPORT_LANGUAGE=fr\n" if source == "file" else "")
    if source == "process":
        monkeypatch.setenv("BROWSER_EVAL_REPORT_LANGUAGE", "fr")
    args = cli.build_parser().parse_args(["run", "--output", str(tmp_path / "run"), "--env-file", str(env)])
    with pytest.raises(ValueError, match="BROWSER_EVAL_REPORT_LANGUAGE"):
        cli.command_run(args)
    assert not args.output.exists()


def test_rescore_rejects_invalid_language_before_rewriting_evidence(tmp_path, monkeypatch):
    from harness.web_fetch import cli

    monkeypatch.setenv("BROWSER_EVAL_REPORT_LANGUAGE", "fr")
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid language must not rescore evidence")
    monkeypatch.setattr(cli, "rescore_evaluation", unexpected)
    args = cli.build_parser().parse_args(["rescore", str(tmp_path / "run"), "--dataset", str(tmp_path / "unused.json")])
    with pytest.raises(ValueError, match="BROWSER_EVAL_REPORT_LANGUAGE"):
        cli.command_rescore(args)


def test_cli_invalid_language_exits_with_configuration_error_before_calls(tmp_path, monkeypatch, capsys):
    from harness.web_fetch import cli
    monkeypatch.setenv("BROWSER_EVAL_REPORT_LANGUAGE", "fr")
    monkeypatch.setattr(sys, "argv", ["browser-harness-eval", "run", "--output", str(tmp_path / "unused")])
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid language must not call providers")
    monkeypatch.setattr(cli, "run_evaluation", unexpected)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "BROWSER_EVAL_REPORT_LANGUAGE" in capsys.readouterr().err
    assert not (tmp_path / "unused").exists()
