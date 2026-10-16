import json
import base64
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.runtime_identity import _command_executable
from harness.web_fetch.providers.subprocess_jsonl import SubprocessJsonlProvider


class SubprocessProviderTest(unittest.TestCase):
    def test_empty_repo_path_fails_preflight(self):
        provider = SubprocessJsonlProvider("test", {"command": ["node"], "repo_path": ""})
        self.assertIn("repo_path", provider.validate())

    def test_runtime_identity_requires_exact_clean_checkout_and_node(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            source = repo / "source.ts"
            source.write_text("export const value = 1;\n")
            subprocess.run(["git", "-C", str(repo), "add", "source.ts"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
            head = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
            node = repo / "node"
            node.write_text("#!/bin/sh\nprintf 'v24.15.0\\n'\n")
            os.chmod(node, 0o755)
            pnpm = repo / "pnpm"
            pnpm.write_text("#!/bin/sh\nprintf '12.1.0\\n'\n")
            os.chmod(pnpm, 0o755)
            provider = SubprocessJsonlProvider(
                "worker",
                {
                    "repo_path": str(repo),
                    "command": [str(node), "worker.mts"],
                    "expected_repo_revision": head,
                    "expected_node_version": "v24.15.0",
                    "package_manager_command": str(pnpm),
                    "expected_package_manager_version": "12.1.0",
                    "required_paths": ["{repo_path}/source.ts"],
                },
            )
            self.assertEqual(provider.validate(), [])
            identity = provider._implementation_identity
            self.assertEqual(identity["repository"], {"head": head, "tracked_status": ""})
            self.assertEqual(identity["node"]["version"], "v24.15.0")
            self.assertEqual(identity["package_manager"]["version"], "12.1.0")
            self.assertEqual(set(identity["files_sha256"]), {"repo/source.ts"})
            self.assertNotIn(str(repo), json.dumps(identity))

            provider.config["expected_repo_revision"] = "0" * 40
            self.assertIn("repo revision " + "0" * 40, provider.validate())
            provider.config["expected_repo_revision"] = head
            provider.config["expected_node_version"] = "v0.0.0"
            self.assertIn("Node v0.0.0", provider.validate())
            provider.config["expected_node_version"] = "v24.15.0"
            provider.config["expected_package_manager_version"] = "0.0.0"
            self.assertIn("package manager 0.0.0", provider.validate())
            self.assertFalse(hasattr(provider, "_implementation_identity"))
            provider.config["expected_package_manager_version"] = "12.1.0"
            source.write_text("dirty\n")
            self.assertIn("clean tracked checkout", provider.validate())

    def test_missing_command_executable_fails_preflight(self):
        provider = SubprocessJsonlProvider(
            "worker", {"repo_path": ".", "command": ["/definitely/missing/worker"]}
        )
        self.assertIn("command executable", provider.validate())

    def test_command_executable_preserves_virtualenv_symlink_path(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            runtime = repo / "runtime"
            runtime.write_text("#!/bin/sh\n")
            os.chmod(runtime, 0o755)
            virtualenv_python = repo / "venv" / "bin" / "python"
            virtualenv_python.parent.mkdir(parents=True)
            virtualenv_python.symlink_to(runtime)

            executable = _command_executable(["venv/bin/python"], repo, lambda value: str(value))

            self.assertEqual(executable, virtualenv_python.absolute())
            self.assertNotEqual(executable, runtime.resolve())

    def test_named_native_runtime_cannot_omit_declared_identity(self):
        provider = SubprocessJsonlProvider(
            "worker",
            {
                "method_type": "openclaw_builtin",
                "repo_path": ".",
                "command": [sys.executable],
            },
        )
        errors = provider.validate()
        self.assertIn("expected_repo_revision", errors)
        self.assertIn("expected_node_version", errors)
        self.assertIn("expected_package_manager_version", errors)
        self.assertIn("required runtime paths", errors)

    def test_worker_receives_shared_limits_and_retains_full_response_envelope(self):
        script = "import sys,json; [(lambda q: print(json.dumps({'ok':True,'content':'answer','metadata':{'request':q},'auxiliary':'retained'}),flush=True))(json.loads(line)) for line in sys.stdin]"
        provider=SubprocessJsonlProvider('worker', {'command':[sys.executable,'-u','-c',script], 'max_bytes':123456})
        self.addCleanup(provider.__del__)
        result=provider.fetch(EvalCase('control','control','https://example.com','control',[]),7)
        payload=json.loads(base64.b64decode(result.metadata['_raw_response_b64']))
        self.assertEqual(payload['metadata']['request']['timeout_seconds'],7)
        self.assertEqual(payload['metadata']['request']['max_bytes'],123456)
        self.assertEqual(payload['content'],result.content)
        self.assertEqual(payload['auxiliary'],'retained')
        with tempfile.TemporaryDirectory() as directory:
            from harness.web_fetch.runner import persist_result_artifacts
            target=Path(directory)
            persist_result_artifacts(target, Path('artifacts/worker/control.txt'), result)
            self.assertEqual(json.loads((target/result.metadata['raw_response_path']).read_bytes()),payload)

    def test_persistence_rejects_answer_edit_or_candidate_filtering(self):
        import hashlib
        from harness.web_fetch.models import ProviderResult
        from harness.web_fetch.runner import persist_result_artifacts
        from harness.web_fetch.response_input import project_native_answer
        cases = [
            ({'content': 'Body | Saved to: /Users/person/work/page.txt'},
             {'schema_version': 1, 'content_pointer': ['content'], 'content_format': 'text'}),
            ({'result': [{'selector': 'a[href]', 'results': [{'attributes': [
                {'name': 'href', 'value': '/opaque-resource'},
                {'name': 'href', 'value': '/about'},
                {'name': 'title', 'value': 'Native label'}]}]}]},
             {'schema_version': 1, 'content_pointer': ['result'], 'content_format': 'json',
              'answer_fields': {'videos': {'pointer': ['result'],
                  'format': 'html_attribute_urls', 'base_url': 'https://example.test/'}}}),
        ]
        for payload, declaration in cases:
            content, fields = project_native_answer(payload, declaration)
            raw = json.dumps(payload).encode()
            for defect in (None, 'edited_text', 'filtered_field', 'missing_raw'):
                if defect == 'filtered_field' and not fields:
                    continue
                with self.subTest(format=declaration['content_format'], defect=defect):
                    metadata = {**fields, 'native_answer_projection': declaration,
                        '_raw_response_b64': base64.b64encode(raw).decode(),
                        'raw_response_sha256': hashlib.sha256(raw).hexdigest(),
                        'raw_response_bytes': len(raw)}
                    answer = content
                    if defect == 'edited_text': answer = content.replace('/about', '') + 'changed'
                    if defect == 'filtered_field': metadata['videos'] = metadata['videos'][:1]
                    if defect == 'missing_raw': metadata.pop('_raw_response_b64')
                    result = ProviderResult(True, answer, metadata=metadata)
                    with tempfile.TemporaryDirectory() as directory:
                        target = Path(directory)/'artifacts/control/result.txt'
                        if defect:
                            with self.assertRaisesRegex(ValueError, 'native'):
                                persist_result_artifacts(Path(directory),
                                    Path('artifacts/control/result.txt'), result)
                            self.assertFalse(target.exists())
                        else:
                            persist_result_artifacts(Path(directory),
                                Path('artifacts/control/result.txt'), result)
                            self.assertEqual(target.read_text(), content)
                            if declaration['content_format'] == 'json':
                                self.assertEqual(json.loads(content), payload['result'])

    def test_deadline_discards_answer_without_discarding_native_evidence(self):
        import hashlib
        from harness.web_fetch.models import ProviderResult
        from harness.web_fetch.runner import persist_result_artifacts
        raw = json.dumps({'content': 'late native answer'}).encode()
        answer = ProviderResult(False, '', error_type='timeout', metadata={
            'native_answer_projection': {'schema_version': 1,
                'content_pointer': ['content'], 'content_format': 'text'},
            '_raw_response_b64': base64.b64encode(raw).decode(),
            'raw_response_sha256': hashlib.sha256(raw).hexdigest(),
            'raw_response_bytes': len(raw)})
        with tempfile.TemporaryDirectory() as directory:
            persist_result_artifacts(Path(directory), Path('artifacts/worker/late.txt'), answer)
            self.assertEqual((Path(directory)/'artifacts/worker/late.txt').read_bytes(), b'')
            self.assertEqual((Path(directory)/answer.metadata['raw_response_path']).read_bytes(), raw)

    def test_native_langchain_document_is_retained_without_changing_answer(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Fixture(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><head><title>Fixture</title></head><body><p>Answer evidence</p></body></html>"
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        worker = Path(__file__).resolve().parents[1] / 'harness/web_fetch/workers/langchain_webbase_worker.py'
        provider = SubprocessJsonlProvider('fixture', {'command': [sys.executable, str(worker)]})
        self.addCleanup(provider.__del__)
        url = f'http://127.0.0.1:{server.server_port}/'
        result = provider.fetch(EvalCase('fixture', 'fixture', url, 'control', []), 45)
        self.assertTrue(result.ok, result.error_message)
        envelope = json.loads(base64.b64decode(result.metadata['_raw_response_b64']))
        self.assertEqual(envelope['raw_tool_response'][0]['page_content'], result.content)
        self.assertEqual(envelope['raw_tool_response'][0]['metadata']['title'], 'Fixture')
        self.assertEqual(result.metadata['timeout_seconds'], 45)

    def test_worker_file_bytes_round_trip(self):
        script = "import sys,json; [(print(json.dumps({'ok':True,'files':[{'base64':'iVBORw0KGgo='}]}),flush=True)) for line in sys.stdin]"
        provider = SubprocessJsonlProvider(
            "worker", {"type": "subprocess_jsonl", "command": [sys.executable, "-u", "-c", script]}
        )
        self.addCleanup(provider.__del__)
        case = EvalCase("file", "file", "https://example.com", "file", [], task_kind="file")
        self.assertEqual(provider.fetch(case, 2).files, [{"data": b"\x89PNG\r\n\x1a\n"}])

    def test_worker_receives_track_and_normalized_retrieval_plan(self):
        script = (
            "import json,sys; "
            "[(lambda request: print(json.dumps({'ok':True,'content':json.dumps(request)}),"
            "flush=True))(json.loads(line)) for line in sys.stdin]"
        )
        provider = SubprocessJsonlProvider(
            "worker", {"type": "subprocess_jsonl", "command": [sys.executable, "-u", "-c", script]}
        )
        self.addCleanup(provider.__del__)
        hints = {
            "version": 1,
            "wait_until": "domcontentloaded",
            "completion": {
                "selector": "main",
                "minimum_count": 1,
                "timeout_ms": 1000,
                "delay_ms": 0,
            },
        }
        case = EvalCase(
            "x", "x", "https://example.com", "dynamic", [], track="dynamic", retrieval_hints=hints
        )
        request = json.loads(provider.fetch(case, 2).content)
        self.assertEqual(request["track"], "dynamic")
        self.assertNotIn("retrieval_hints", request)
        self.assertEqual(request["retrieval_plan"]["wait_kind"], "selector")
        self.assertEqual(request["retrieval_plan"]["wait_value"], "main")

    def test_dynamic_worker_is_not_started_without_hints(self):
        provider = SubprocessJsonlProvider(
            "worker", {"type": "subprocess_jsonl", "command": ["/must/not/start"]}
        )
        case = EvalCase("x", "x", "https://example.com", "dynamic", [], track="dynamic")
        result = provider.fetch(case, 2)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "configuration")

    def test_persistent_worker_ignores_diagnostic_stdout(self) -> None:
        script = (
            "import json,sys; "
            "[(print('diagnostic',flush=True), print(json.dumps({'ok':True,'content':'Example Domain','final_url':json.loads(line)['url']}),flush=True)) for line in sys.stdin]"
        )
        provider = SubprocessJsonlProvider(
            "worker", {"type": "subprocess_jsonl", "command": [sys.executable, "-u", "-c", script]}
        )
        case = EvalCase(
            id="x",
            name="x",
            url="https://example.com",
            category="static",
            expected_any=["Example Domain"],
        )
        result = provider.fetch(case, 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "Example Domain")
        self.assertEqual(result.metadata["worker_logs"], ["diagnostic"])
        provider.__del__()

    def test_stderr_is_request_scoped_and_not_a_global_tail(self) -> None:
        script = (
            "import json,sys; "
            "[(lambda r: (print('stderr-for-'+r['url'],file=sys.stderr,flush=True),"
            "print(json.dumps({'ok':True,'content':'ok'}),flush=True)))(json.loads(line)) "
            "for line in sys.stdin]"
        )
        provider = SubprocessJsonlProvider(
            "worker", {"type": "subprocess_jsonl", "command": [sys.executable, "-u", "-c", script]}
        )
        self.addCleanup(provider.__del__)
        first = provider.fetch(EvalCase("one", "one", "https://one.example", "static", []), 2)
        second = provider.fetch(EvalCase("two", "two", "https://two.example", "static", []), 2)
        self.assertIn("one.example", first.metadata["stderr"])
        self.assertNotIn("two.example", first.metadata["stderr"])
        self.assertIn("two.example", second.metadata["stderr"])
        self.assertNotIn("one.example", second.metadata["stderr"])
        self.assertFalse(first.metadata["stderr_truncated"])
        self.assertGreater(first.metadata["stderr_bytes"], 0)

    def test_stderr_keeps_more_than_legacy_two_hundred_lines(self) -> None:
        script = (
            "import json,sys; "
            "[( [print('line-%03d'%i,file=sys.stderr) for i in range(250)],"
            "print(json.dumps({'ok':True,'content':'ok'}),flush=True),sys.stderr.flush()) "
            "for line in sys.stdin]"
        )
        provider = SubprocessJsonlProvider(
            "worker", {"type": "subprocess_jsonl", "max_bytes": 100_000,
                       "command": [sys.executable, "-u", "-c", script]}
        )
        self.addCleanup(provider.__del__)
        result = provider.fetch(EvalCase("x", "x", "https://example.com", "static", []), 2)
        self.assertIn("line-000", result.metadata["stderr"])
        self.assertIn("line-249", result.metadata["stderr"])
        self.assertFalse(result.metadata["stderr_truncated"])


if __name__ == "__main__":
    unittest.main()


# These controls operate only on synthetic saved responses; no provider calls.
import pytest


@pytest.mark.parametrize('field', ['images', 'links', 'videos', 'media', 'extras'])
@pytest.mark.parametrize('declare_content', [False, True])
def test_undeclared_score_fields_cannot_enter_persistence(tmp_path, field, declare_content):
    import hashlib
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.runner import persist_result_artifacts
    raw = b'{"content":"answer","metadata":{"images":["https://example.test/native.png"]}}'
    metadata = {field: ['https://example.test/forged.png'],
                '_raw_response_b64': base64.b64encode(raw).decode(),
                'raw_response_sha256': hashlib.sha256(raw).hexdigest(), 'raw_response_bytes': len(raw)}
    if declare_content:
        metadata['native_answer_projection'] = {'schema_version': 1,
            'content_pointer': ['content'], 'content_format': 'text'}
    with pytest.raises(ValueError, match='undeclared'):
        persist_result_artifacts(tmp_path, Path('artifacts/test.txt'), ProviderResult(True, 'answer', metadata=metadata))
    assert not (tmp_path/'artifacts/test.txt').exists()


@pytest.mark.parametrize('defect', [None, 'replace_image', 'drop_wrong_image', 'forge_file'])
def test_native_worker_fields_and_files_are_exactly_bound(tmp_path, defect):
    import hashlib
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.runner import persist_result_artifacts
    from harness.web_fetch.call_boundary import prepare_answer
    payload = {'content': 'answer', 'metadata': {'images': ['https://example.test/good.png',
                                                         'https://example.test/wrong.png']},
               'files': [{'base64': base64.b64encode(b'original file bytes').decode()}]}
    raw = json.dumps(payload).encode()
    metadata = {**payload['metadata'], 'native_answer_projection': {'schema_version': 1,
        'content_pointer': ['content'], 'content_format': 'text',
        'answer_fields': {'images': {'pointer': ['metadata', 'images'], 'format': 'identity'}},
        'files': {'pointer': ['files'], 'format': 'worker_files'}},
        '_raw_response_b64': base64.b64encode(raw).decode(),
        'raw_response_sha256': hashlib.sha256(raw).hexdigest(), 'raw_response_bytes': len(raw)}
    files = [{'data': b'original file bytes'}]
    if defect == 'replace_image': metadata['images'] = ['https://example.test/forged.png']
    if defect == 'drop_wrong_image': metadata['images'] = metadata['images'][:1]
    if defect == 'forge_file': files = [{'data': b'forged file bytes'}]
    result = prepare_answer(ProviderResult(True, 'answer', metadata=metadata, files=files),
                           EvalCase('case', 'Case', 'https://example.test/', 'control', []))
    if defect:
        with pytest.raises(ValueError, match='projection'):
            persist_result_artifacts(tmp_path, Path('artifacts/test.txt'), result)
    else:
        persist_result_artifacts(tmp_path, Path('artifacts/test.txt'), result)
        from harness.web_fetch.artifacts import verify_record_artifacts
        record = {'attempt_id': 'test', 'ok': True, 'artifact_path': 'artifacts/test.txt',
                  'metrics': {'content_sha256': hashlib.sha256(b'answer').hexdigest()},
                  'provider_metadata': result.metadata}
        verify_record_artifacts(tmp_path, record)
        result.metadata['file_evaluation']['pass'] = True
        with pytest.raises(ValueError, match='file evaluation'):
            verify_record_artifacts(tmp_path, record)
        result.metadata['file_evaluation']['pass'] = False
        result.metadata['images'] = ['https://example.test/forged.png']
        with pytest.raises(ValueError, match='projection'):
            verify_record_artifacts(tmp_path, record)


def test_file_scoring_recomputes_acceptance_from_native_bytes():
    from harness.web_fetch.scoring import score_attempt
    from harness.web_fetch.metrics import task_passed
    raw = json.dumps({'content': '', 'files': [{'base64': base64.b64encode(b'not a PNG').decode()}]}).encode()
    metadata = {'file_evaluation': {'pass': True}, 'native_answer_projection': {'schema_version': 1,
        'content_pointer': ['content'], 'content_format': 'text',
        'files': {'pointer': ['files'], 'format': 'worker_files'}}}
    metrics = score_attempt({'url': 'https://example.test/', 'task_kind': 'file'}, b'', ok=True,
        metadata=metadata, previous_metrics={'raw_file_pass': True}, native_response=raw,
        native_files=[{'data': b'not a PNG'}])
    assert metrics['raw_file_pass'] is False
    assert not task_passed(True, metrics, 'file')


def test_byte_projection_cannot_claim_unverified_structured_metadata():
    from harness.web_fetch.response_input import require_native_answer_projection
    metadata = {'images': ['https://example.test/forged.png'], 'native_answer_projection': {
        'schema_version': 1, 'source_format': 'bytes', 'content_format': 'utf8',
        'answer_fields': {'images': {'pointer': ['images'], 'format': 'identity'}}}}
    with pytest.raises(ValueError, match='cannot declare'):
        require_native_answer_projection('native stdout', metadata, b'native stdout')
