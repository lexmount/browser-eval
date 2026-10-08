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
    assert paths == ['/good', '/wrong', '/invalid']
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
