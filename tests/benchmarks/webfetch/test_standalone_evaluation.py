"""A saved answer must satisfy its task independently of any execution harness."""
import hashlib
import json
import subprocess
import sys

import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed, task_measured
from lexbench_webfetch.scoring_regressions import active_scoring_regressions


@pytest.mark.parametrize("identifier,row", active_scoring_regressions().items())
def test_declared_acceptance_scenarios(identifier, row):
    arguments = dict(ok=row['ok'], metadata=row.get('metadata'), title=row.get('title'),
                     final_url=row.get('final_url'))
    if row.get('expected_validation_error'):
        with pytest.raises(ValueError, match=row['expected_validation_error']):
            score_attempt(row['case'], row['artifact'].encode(), **arguments)
        assert row['expected_task_pass'] is False
        return
    metrics = score_attempt(row['case'], row['artifact'].encode(), **arguments)
    assert task_passed(row['ok'], metrics, row['case'].get('task_kind', 'body')) == row['expected_task_pass']
    if 'expected_task_measured' in row:
        assert task_measured(metrics) == row['expected_task_measured']
    for key, expected in row.get('expected_metrics', {}).items():
        assert metrics.get(key) == expected, (identifier, key)


@pytest.mark.parametrize("kind,requirements,answer,passed", [
    ('body', {'expected_any': ['Alpha article']}, b'Alpha article', True),
    ('body', {'expected_any': ['Alpha article']}, b'Login required', False),
    ('structure', {'acceptance_contract': {
        'version': 1, 'schema': 'named_link', 'minimum_records': 2,
        'hosts': ['example.test'], 'entry_path': r'/item/\d+', 'stable_text_anchors': []}},
        b'[Alpha](/item/1)\n[Beta](/item/2)', True),
    ('structure', {'acceptance_contract': {
        'version': 1, 'schema': 'named_link', 'minimum_records': 2,
        'hosts': ['example.test'], 'entry_path': r'/item/\d+', 'stable_text_anchors': []}},
        b'[Alpha](/item/1)', False),
    ('resource', {'content_score_applicable': False,
                  'expected_resources': [{'id': 'image', 'urls': ['/image.png']}]},
        b'![Image](/image.png)', True),
    ('resource', {'content_score_applicable': False,
                  'expected_resources': [{'id': 'image', 'urls': ['/image.png']}]},
        b'No resource delivered', False),
])
def test_standalone_cli_accepts_and_rejects_task_deliveries(tmp_path, kind, requirements, answer, passed):
    task = {'id': 'sample', 'name': 'Sample', 'url': 'https://example.test/page',
            'category': 'test', 'task_kind': kind, 'expected_any': [], **requirements}
    (tmp_path/'task.json').write_text(json.dumps(task))
    (tmp_path/'answer.txt').write_bytes(answer)
    result = subprocess.run([sys.executable, '-m', 'lexbench_webfetch.cli', 'score',
                             '--task', str(tmp_path/'task.json'),
                             '--response', str(tmp_path/'answer.txt')],
                            cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['passed'] is passed


@pytest.mark.parametrize('valid', [True, False])
def test_file_acceptance_uses_delivered_bytes_and_declared_hash(tmp_path, valid):
    good = b'\x89PNG\r\n\x1a\n' + b'x' * 32
    task = {'url': 'https://example.test/image', 'task_kind': 'file', 'expected_any': [],
            'content_score_applicable': False,
            'source_verification': {'mime': 'image/png', 'file_sha256': hashlib.sha256(good).hexdigest()}}
    raw = good if valid else good[:-1] + b'y'
    (tmp_path/'file.bin').write_bytes(raw)
    (tmp_path/'task.json').write_text(json.dumps(task))
    (tmp_path/'answer.txt').write_text('file attached')
    result = subprocess.run([sys.executable, '-m', 'lexbench_webfetch.cli', 'score',
                             '--task', str(tmp_path/'task.json'), '--response', str(tmp_path/'answer.txt'),
                             '--file', str(tmp_path/'file.bin')], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['passed'] is valid
    assert not evaluate_response(task, b'', metadata={'file_evaluation': {'pass': True}})['passed']


@pytest.mark.parametrize('envelope,expected', [(b'{"success":true,"result":""}', True),
                                               (b'{"success":false,"error":"unavailable"}', False)])
def test_file_verdict_follows_normalized_request_state(envelope, expected):
    task = {'url': 'https://example.test/file', 'task_kind': 'file',
            'expected_any': [], 'content_score_applicable': False}
    result = evaluate_response(task, envelope, files=[b'\x89PNG\r\n\x1a\n'+b'x'*32],
                               ok=False, status_code=200)
    assert result['metrics']['request_ok'] is expected
    assert result['passed'] is expected


@pytest.mark.parametrize("kind,metadata,passed", [
    ("structure", {"links": [{"text": "Alpha", "href": "/item/1"},
                               {"text": "Beta", "href": "/item/2"}]}, True),
    ("structure", {"links": [{"text": "Alpha", "href": "/item/1"}]}, False),
    ("resource", {"images": ["/target.png"]}, True),
    ("resource", {"images": ["/wrong.png"]}, False),
    ("file", {"file_evaluation": {"pass": True}}, False),
])
def test_cli_preserves_structured_answer_evidence(tmp_path, kind, metadata, passed):
    task = {"id": "sample", "url": "https://example.test/page", "task_kind": kind,
            "expected_any": [], "content_score_applicable": False}
    if kind == "structure":
        task["acceptance_contract"] = {"version": 1, "schema": "named_link",
            "minimum_records": 2, "hosts": ["example.test"], "entry_path": r"/item/\d+",
            "stable_text_anchors": []}
    elif kind == "resource":
        task["expected_resources"] = [{"id": "target", "urls": ["/target.png"]}]
    (tmp_path / "task.json").write_text(json.dumps(task))
    (tmp_path / "answer.txt").write_text("")
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    expected = evaluate_response(task, b"", metadata=metadata)
    assert expected["passed"] is passed
    result = subprocess.run([sys.executable, "-m", "lexbench_webfetch.cli", "score",
        "--task", str(tmp_path / "task.json"), "--response", str(tmp_path / "answer.txt"),
        "--metadata", str(tmp_path / "metadata.json")], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("metadata", [[], "invalid", None, 42])
def test_cli_rejects_non_object_answer_metadata(tmp_path, metadata):
    (tmp_path / "task.json").write_text('{"url":"https://example.test","expected_any":[]}')
    (tmp_path / "answer.txt").write_text("content")
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    result = subprocess.run([sys.executable, "-m", "lexbench_webfetch.cli", "score",
        "--task", str(tmp_path / "task.json"), "--response", str(tmp_path / "answer.txt"),
        "--metadata", str(tmp_path / "metadata.json")], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 1
    assert "metadata must be a JSON object" in result.stderr
    assert "Traceback" not in result.stderr


def test_explicit_delivered_files_recompute_acceptance_over_prior_metrics():
    metrics = score_attempt({'url': 'https://example.test/', 'task_kind': 'file'}, b'', ok=True,
        metadata={'file_evaluation': {'pass': True}}, previous_metrics={'raw_file_pass': True},
        delivered_files=[b'not a PNG'])
    assert metrics['raw_file_pass'] is False
    assert not task_passed(True, metrics, 'file')
