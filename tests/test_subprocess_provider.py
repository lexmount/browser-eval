import json
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
        provider = SubprocessJsonlProvider('test', {'command': ['node'], 'repo_path': ''})
        self.assertIn('repo_path', provider.validate())

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
            provider = SubprocessJsonlProvider("worker", {
                "repo_path": str(repo), "command": [str(node), "worker.mts"],
                "expected_repo_revision": head, "expected_node_version": "v24.15.0",
                "required_paths": ["{repo_path}/source.ts"],
            })
            self.assertEqual(provider.validate(), [])
            identity = provider._implementation_identity
            self.assertEqual(identity["repository"], {"head": head, "tracked_status": ""})
            self.assertEqual(identity["node"]["version"], "v24.15.0")
            self.assertEqual(set(identity["files_sha256"]), {"repo/source.ts"})
            self.assertNotIn(str(repo), json.dumps(identity))

            provider.config["expected_repo_revision"] = "0" * 40
            self.assertIn("repo revision " + "0" * 40, provider.validate())
            provider.config["expected_repo_revision"] = head
            provider.config["expected_node_version"] = "v0.0.0"
            self.assertIn("Node v0.0.0", provider.validate())
            provider.config["expected_node_version"] = "v24.15.0"
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

            executable = _command_executable(
                ["venv/bin/python"], repo, lambda value: str(value)
            )

            self.assertEqual(executable, virtualenv_python.absolute())
            self.assertNotEqual(executable, runtime.resolve())

    def test_named_native_runtime_cannot_omit_locked_identity(self):
        provider = SubprocessJsonlProvider("worker", {
            "method_type": "openclaw_builtin", "repo_path": ".", "command": [sys.executable],
        })
        errors = provider.validate()
        self.assertIn("repo revision 12c4bdf7482f24c85b011e9d858fdec3c245101c", errors)
        self.assertIn("Node v24.15.0", errors)
        self.assertIn("locked required paths", errors)

    def test_worker_file_bytes_round_trip(self):
        script = "import sys,json; [(print(json.dumps({'ok':True,'files':[{'base64':'iVBORw0KGgo='}]}),flush=True)) for line in sys.stdin]"
        provider = SubprocessJsonlProvider("worker", {"type":"subprocess_jsonl", "command":[sys.executable,"-u","-c",script]})
        self.addCleanup(provider.__del__)
        case = EvalCase("file", "file", "https://example.com", "file", [], task_kind="file")
        self.assertEqual(provider.fetch(case,2).files,[{"data":b"\x89PNG\r\n\x1a\n"}])

    def test_persistent_worker_ignores_diagnostic_stdout(self) -> None:
        script = (
            "import json,sys; "
            "[(print('diagnostic',flush=True), print(json.dumps({'ok':True,'content':'Example Domain','final_url':json.loads(line)['url']}),flush=True)) for line in sys.stdin]"
        )
        provider = SubprocessJsonlProvider("worker", {"type": "subprocess_jsonl", "command": [sys.executable, "-u", "-c", script]})
        case = EvalCase(id="x", name="x", url="https://example.com", category="static", expected_any=["Example Domain"])
        result = provider.fetch(case, 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "Example Domain")
        self.assertEqual(result.metadata["worker_logs"], ["diagnostic"])
        provider.__del__()


if __name__ == "__main__":
    unittest.main()
