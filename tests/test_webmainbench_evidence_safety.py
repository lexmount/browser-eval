"""Reject untrusted artifact paths before copying WebMainBench evidence."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.assemble_webmainbench_evidence import copy_scored_artifact as assemble_copy
from scripts.subset_webmainbench_evidence import copy_scored_artifact as subset_copy


class WebMainBenchEvidenceSafetyTests(unittest.TestCase):
    def test_valid_artifact_copies_and_escaping_paths_do_not(self) -> None:
        for copy in (assemble_copy, subset_copy):
            with self.subTest(copy=copy.__module__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source"
                output = root / "output"
                (source / "artifacts").mkdir(parents=True)
                (source / "artifacts" / "answer.md").write_bytes(b"answer")
                row = {
                    "track_id": "case",
                    "artifact_path": "artifacts/answer.md",
                    "output_sha256": hashlib.sha256(b"answer").hexdigest(),
                }
                copy(source, output, row)
                self.assertEqual((output / row["artifact_path"]).read_bytes(), b"answer")
                for path in ("../outside.md", "/tmp/outside.md", "artifacts/../../outside.md"):
                    with self.subTest(path=path), self.assertRaises(ValueError):
                        copy(source, output, {**row, "artifact_path": path})
                self.assertFalse((root / "outside.md").exists())

                outside = root / "outside"
                outside.mkdir()
                (source / "artifacts" / "linked").mkdir()
                (source / "artifacts" / "linked" / "answer.md").write_bytes(b"answer")
                (output / "artifacts" / "linked").symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "escapes"):
                    copy(source, output, {**row, "artifact_path": "artifacts/linked/answer.md"})
                self.assertFalse((outside / "answer.md").exists())


if __name__ == "__main__":
    unittest.main()
