import json
import base64
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lexbench_webfetch.models import EvalCase
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
