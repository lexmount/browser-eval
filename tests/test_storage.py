import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.storage import (
    current_evidence_dir,
    data_home,
    require_canonical_inputs,
    require_evidence,
    research_evidence_dir,
)


class StorageTests(unittest.TestCase):
    def test_explicit_data_home_and_evidence_override(self):
        with patch.dict(os.environ, {"BROWSER_EVAL_DATA_HOME": "/tmp/browser-data"}, clear=False):
            self.assertEqual(data_home(), Path("/tmp/browser-data").resolve())
        with patch.dict(os.environ, {"WEB_FETCH_EVIDENCE_DIR": "/tmp/browser-evidence"}, clear=False):
            evidence = Path("/tmp/browser-evidence").resolve()
            self.assertEqual(current_evidence_dir(), evidence)
            self.assertEqual(research_evidence_dir(), evidence.parent / "research")

    def test_explicit_research_override(self):
        with patch.dict(
            os.environ,
            {"WEB_FETCH_RESEARCH_EVIDENCE_DIR": "/tmp/browser-research"},
            clear=False,
        ):
            self.assertEqual(research_evidence_dir(), Path("/tmp/browser-research").resolve())

    def test_required_bundle_rejects_partial_data(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(FileNotFoundError,'results.jsonl'):
                require_evidence(Path(folder))

    def test_canonical_inputs_must_match_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            canonical = root / "canonical"
            evidence = root / "evidence"
            for base in (canonical, evidence):
                (base / "sources/case").mkdir(parents=True)
                (base / "list-references").mkdir()
                (base / "dataset.json").write_bytes(b"dataset")
                (base / "sources/case/reference.txt").write_bytes(b"reference")
                (base / "list-references/case.json").write_bytes(b"list")
            require_canonical_inputs(evidence, canonical)
            (evidence / "sources/case/reference.txt").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "sources"):
                require_canonical_inputs(evidence, canonical)


if __name__ == "__main__":
    unittest.main()
