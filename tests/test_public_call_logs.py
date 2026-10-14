import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.artifacts import (
    record_public_call_log_bindings, verify_record_public_call_logs,
)
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import run_evaluation


CASE = EvalCase(
    "case", "Case", "https://user:password@example.com/page?view=full&api_token=secret#part",
    "static", ["Expected"],
)


def run(root: Path, provider: Provider, *, retry: int = 0):
    return run_evaluation(
        output_dir=root, dataset_meta={"id": "call-log-test"},
        dataset_path=Path("dataset.json"), cases=[CASE], providers=[provider],
        provider_config_path=Path("providers.json"), runs=1, timeout=2, delay=0,
        max_failure_retries=retry,
    )[0][0]


def load_log(root: Path, binding: dict):
    return json.loads((root / binding["path"]).read_text())


class ResultProvider(Provider):
    def __init__(self, result):
        super().__init__("provider", {"type": "fixture"})
        self.result = result

    def fetch(self, case, timeout):
        return self.result


class PublicCallLogTests(unittest.TestCase):
    def test_common_sensitive_fields_are_redacted_from_public_log(self):
        secret_lines = "\n".join([
            'refresh_token=refresh-secret',
            'AWS_SECRET_ACCESS_KEY=aws-secret',
            'X-Api-Key: header-secret',
            '{"client_secret":"json-secret","safe":"visible"}',
            'curl failed: https://example.test/api?access_token=query-secret&view=full',
        ])
        result = ProviderResult(
            True, "Expected", metadata={"stderr": secret_lines, "exit_code": 0}
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run(root, ResultProvider(result))
            serialized = (root / record["provider_metadata"]["call_log"]["path"]).read_text()
            for secret in ("refresh-secret", "aws-secret", "header-secret", "json-secret",
                           "query-secret"):
                self.assertNotIn(secret, serialized)
            self.assertIn('\\"safe\\":\\"visible\\"', serialized)
            self.assertIn('view=full', serialized)

    def test_success_has_closed_complete_hash_bound_log(self):
        raw = b'{"private":"Bearer never-publish"}'
        result = ProviderResult(
            True, "Expected", status_code=200, final_url="https://example.com/final#fragment",
            metadata={
                "route": "advanced_extract", "content_type": "text/markdown",
                "request_id": "request-123", "exit_code": 0,
                "request_method": "POST",
                "request_summary": {
                    "format": "markdown", "wait_ms": 500,
                    "headers": {"Authorization": "Bearer never-publish"},
                    "api_token": "never-publish",
                },
                "stdout": "adapter ready",
                "stdout_bytes": len("adapter ready"),
                "stderr": "warning at /Users/person/private CODEX_TOKEN=never-publish",
                "stderr_bytes": 72,
                "stderr_truncated": True,
                "_raw_response_b64": base64.b64encode(raw).decode(),
                "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_response_bytes": len(raw),
                # Deliberately excluded by the public allowlist.
                "authorization": "Bearer secret",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run(root, ResultProvider(result))
            binding = record["provider_metadata"]["call_log"]
            log = load_log(root, binding)
            self.assertEqual(log["request"], {
                "method": "POST",
                "route": "advanced_extract",
                "summary": {"format": "markdown", "wait_ms": 500},
                "url": "https://example.com/page?view=full&api_token=%5BREDACTED%5D",
            })
            self.assertEqual(log["response"]["status_code"], 200)
            self.assertEqual(log["response"]["content_type"], "text/markdown")
            self.assertEqual(log["response"]["request_id"], "request-123")
            self.assertEqual(log["process"]["exit_code"], 0)
            self.assertEqual(log["process"]["stdout"], "adapter ready")
            self.assertEqual(log["process"]["stdout_bytes"], len("adapter ready"))
            self.assertFalse(log["process"]["stdout_truncated"])
            self.assertIn("[local-path]", log["process"]["stderr"])
            self.assertIn("[REDACTED]", log["process"]["stderr"])
            self.assertEqual(log["process"]["stderr_bytes"], 72)
            self.assertTrue(log["process"]["stderr_truncated"])
            self.assertEqual(log["capture"]["process_stderr"], "captured_truncated")
            self.assertEqual(
                [event["stage"] for event in log["events"]],
                ["call_started", "response_received", "call_completed"],
            )
            self.assertEqual(log["output"]["sha256"], record["metrics"]["content_sha256"])
            self.assertFalse(log["output"]["empty"])
            self.assertEqual(log["capture"]["raw_response"], "retained_private_hash_bound")
            serialized = (root / binding["path"]).read_text()
            self.assertNotIn("never-publish", serialized)
            self.assertNotIn("/Users/", serialized)
            verify_record_public_call_logs(root, record)

    def test_provider_failure_records_error_and_empty_output(self):
        result = ProviderResult(False, "", status_code=429, error_type="rate_limit",
                                error_message="HTTP 429", metadata={"route": "extract"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run(root, ResultProvider(result))
            log = load_log(root, record["provider_metadata"]["call_log"])
            self.assertEqual(log["error"]["type"], "rate_limit")
            self.assertEqual(log["error"]["message"], "HTTP 429")
            self.assertTrue(log["output"]["empty"])
            self.assertEqual(log["capture"]["record"], "complete")

    def test_exception_is_diagnostic_not_scored_output_and_is_sanitized(self):
        class Broken(Provider):
            def fetch(self, case, timeout):
                raise RuntimeError(
                    "authorization=Bearer-secret CODEX_TOKEN=top-secret "
                    "/Users/person/project/worker.py"
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run(root, Broken("broken", {"type": "fixture"}))
            self.assertEqual((root / record["artifact_path"]).read_bytes(), b"")
            self.assertEqual(record["metrics"]["content_chars"], 0)
            log = load_log(root, record["provider_metadata"]["call_log"])
            self.assertEqual(log["error"]["exception_class"], "RuntimeError")
            self.assertEqual(log["events"][1]["stage"], "exception_raised")
            self.assertTrue(log["error"]["stack"])
            serialized = json.dumps(log)
            self.assertNotIn("top-secret", serialized)
            self.assertNotIn("Bearer-secret", serialized)
            self.assertNotIn("/Users/", serialized)
            self.assertIn("[REDACTED]", serialized)
            self.assertIn("[local-path]", serialized)

    def test_retry_has_one_log_per_physical_call(self):
        class Retried(Provider):
            def __init__(self):
                super().__init__("retried", {"type": "fixture"})
                self.calls = 0

            def fetch(self, case, timeout):
                self.calls += 1
                if self.calls == 1:
                    return ProviderResult(False, "", error_type="network",
                                          metadata={"route": "best_route"})
                return ProviderResult(True, "Expected", metadata={"route": "best_route"})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / "worker"
            record = run(worker, Retried(), retry=1)
            bindings = record_public_call_log_bindings(record)
            self.assertEqual(len(bindings), 2)
            self.assertNotEqual(bindings[0]["path"], bindings[1]["path"])
            self.assertEqual(
                [load_log(worker, binding)["identity"]["physical_attempt"]
                 for binding in bindings],
                [1, 2],
            )
            verify_record_public_call_logs(worker, record)

    def test_tampered_log_or_bound_output_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run(root, ResultProvider(ProviderResult(True, "Expected")))
            binding = record["provider_metadata"]["call_log"]
            (root / binding["path"]).write_text("{}")
            with self.assertRaisesRegex(ValueError, "changed"):
                verify_record_public_call_logs(root, record)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run(root, ResultProvider(ProviderResult(True, "Expected")))
            (root / record["artifact_path"]).write_text("changed")
            with self.assertRaisesRegex(ValueError, "output binding changed"):
                verify_record_public_call_logs(root, record)


if __name__ == "__main__":
    unittest.main()
