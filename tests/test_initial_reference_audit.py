import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.audit_initial_references import audit_case, audit_dataset, reconstruct_quotes_json


class InitialReferenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = {"id": "sample", "name": "Sample", "track": "web",
                     "url": "https://example.test/", "required_facts": [
                         {"id": "body", "alternatives": ["Hello world"]}],
                     "ground_truth_main_content": "Hello world"}

    def source(self, raw=b"<main>Hello world</main>", name="source.html.txt"):
        folder = self.root / "sources/sample"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(raw)
        (folder / "evidence.json").write_text(json.dumps({"sha256": hashlib.sha256(raw).hexdigest()}))
        return folder

    def test_exact_reconstruction_and_hashes_no_body_or_writes(self):
        self.source(b'<meta charset="utf-8"><main>Hello\xe2\x80\x8b world</main>')
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        row = audit_case(self.case, self.root)
        self.assertTrue(row["exact_reconstruction"])
        self.assertEqual(row["selector"], "main")
        self.assertTrue(row["source_hash_matches_metadata"])
        self.assertEqual(row["reference_sha256"], row["reconstructed_sha256"])
        self.assertNotIn("Hello world", json.dumps(row))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertFalse(Path(row['source_path']).is_absolute())
        self.assertTrue(all(not Path(path).is_absolute() for path in row['source_candidates']))

    def test_absent_source_is_unknown_not_false(self):
        row = audit_case(self.case, self.root)
        self.assertEqual(row["source_state"], "absent")
        for key in ("source_path", "source_sha256", "exact_reconstruction", "selector"):
            self.assertIsNone(row[key])

    def test_empty_reference_is_unknown_not_empty_string_match(self):
        self.source(b"<main></main>")
        self.case["ground_truth_main_content"] = ""
        row = audit_case(self.case, self.root)
        self.assertEqual(row["reference_state"], "absent")
        self.assertIsNone(row["reference_sha256"])
        self.assertIsNone(row["exact_reconstruction"])

    def test_extraction_error_reports_type_without_message(self):
        self.source()
        with patch("scripts.audit_initial_references.extract_html", side_effect=ValueError("SECRET")):
            row = audit_case(self.case, self.root)
        self.assertEqual(row["extraction_error_type"], "ValueError")
        self.assertIsNone(row["exact_reconstruction"])
        self.assertNotIn("SECRET", json.dumps(row))

    def test_current_initial_and_rendered_files_are_not_globally_absent(self):
        folder = self.source(name="initial.html.txt")
        (folder / "rendered.txt").write_text("Hello world")
        row = audit_case(self.case, self.root)
        self.assertTrue(row["paired_capture_files_present"])
        self.assertEqual(row["rendered_snapshot_sha256"], row["reference_sha256"])

    def test_quotes_are_decoded_without_javascript(self):
        raw = b'<script>var data = [{"text":"Hello world","author":{"name":"Ada"},"tags":["science"]}]; throw Error("do not run")</script>'
        result = reconstruct_quotes_json(raw, self.case)
        self.assertEqual(result["entries"], 1)
        self.assertTrue(result["metrics"]["content_pass"])
        self.assertFalse(result["js_executed"])
        self.assertNotIn("Hello world", json.dumps(result))

    def test_summary_preserves_unknown_and_detects_mismatches(self):
        folder = self.source()
        (folder / "evidence.json").write_text(json.dumps({"sha256": "wrong"}))
        dataset = self.root / "dataset.json"
        dataset.write_text(json.dumps({"cases": [self.case, {**self.case, "id": "missing"}]}))
        report = audit_dataset(dataset)
        self.assertEqual(report["by_track"]["web"]["exact_reconstructions"], 1)
        self.assertEqual(report["summary"]["unknown_reconstruction_case_ids"], ["missing"])
        self.assertEqual(report["summary"]["source_hash_mismatch_case_ids"], ["sample"])
        self.assertFalse(report["provider_outputs_used"])
        self.assertEqual(report, audit_dataset(dataset))


if __name__ == "__main__":
    unittest.main()
