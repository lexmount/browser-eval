import sys
import unittest

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.subprocess_jsonl import SubprocessJsonlProvider


class SubprocessProviderTest(unittest.TestCase):
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
