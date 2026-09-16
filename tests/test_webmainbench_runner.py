import socket
import unittest
import json
import tempfile
from pathlib import Path

from scripts.run_webmainbench_benchmark import DATASET_SHA256, fetch_record, load_selected_ids


RECORD = {
    "track_id": "case-one",
    "url": "https://example.test/source",
    "html": "<p>Hello</p>",
    "groundtruth_content": "Hello",
    "meta": {},
}


class RaisingCloudflare:
    def fetch_html(self, case, html, timeout):
        raise TimeoutError("read timed out")


class RaisingProvider:
    def fetch(self, case, timeout):
        raise socket.gaierror("name lookup failed")


class WebMainBenchRunnerTests(unittest.TestCase):
    def test_loads_and_binds_frozen_sample_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "dataset_id": "sample-v1",
                "parent": {"sha256": DATASET_SHA256},
                "sampling": {"size": 2, "seed": "fixed"},
                "cases": [{"track_id": "a"}, {"track_id": "b"}],
            }), encoding="utf-8")
            selected, binding = load_selected_ids(path)
            self.assertEqual(selected, {"a", "b"})
            self.assertEqual(binding["format"], "webmainbench_sample_manifest_v1")
            self.assertEqual(binding["case_count"], 2)
            self.assertEqual(binding["selection_seed"], "fixed")

    def test_cloudflare_timeout_becomes_retryable_result(self):
        result = fetch_record(None, RaisingCloudflare(), RECORD, "https://localhost/case", 45)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "timeout")
        self.assertEqual(result.error_message, "read timed out")
        self.assertEqual(result.metadata, {"exception_class": "TimeoutError"})

    def test_local_adapter_exception_is_inspectable(self):
        result = fetch_record(RaisingProvider(), None, RECORD, "https://localhost/case", 45)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_type, "provider_exception")
        self.assertEqual(result.metadata, {"exception_class": "gaierror"})


if __name__ == "__main__":
    unittest.main()
