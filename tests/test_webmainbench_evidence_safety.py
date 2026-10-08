"""Reject untrusted artifact paths before copying WebMainBench evidence."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import pytest

from harness.web_fetch.webmainbench_assembly import copy_bound
from harness.web_fetch.webmainbench_evidence import copy_alias_artifacts
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


@pytest.mark.parametrize("boundary", ["full", "variant"])
@pytest.mark.parametrize(
    "link", [None, "source_file", "source_directory", "target_file", "target_directory"]
)
def test_certified_copy_accepts_bound_bytes_and_rejects_internal_links(tmp_path, boundary, link):
    source, output = tmp_path / "source", tmp_path / "output"
    original = source / "artifacts/original/answer.md"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"immutable measured content")
    target_provider = "variant" if boundary == "variant" else "original"
    target = output / f"artifacts/{target_provider}/answer.md"
    target.parent.mkdir(parents=True)
    if link == "source_file":
        payload = original.with_name("payload.md")
        original.rename(payload)
        original.symlink_to(payload)
    elif link == "source_directory":
        payload = original.parent.with_name("payload")
        original.parent.rename(payload)
        original.parent.symlink_to(payload, target_is_directory=True)
    elif link == "target_file":
        payload = target.with_name("payload.md")
        payload.write_bytes(b"preserve existing content")
        target.symlink_to(payload)
    elif link == "target_directory":
        payload = target.parent.with_name("payload")
        target.parent.rename(payload)
        target.parent.symlink_to(payload, target_is_directory=True)
    relative = "artifacts/original/answer.md"
    digest = hashlib.sha256(b"immutable measured content").hexdigest()

    def copy():
        if boundary == "full":
            copy_bound(source, output, relative, digest)
        else:
            copy_alias_artifacts(
                source,
                output,
                [
                    {
                        "artifact_path": relative,
                        "output_sha256": digest,
                    }
                ],
                "original",
                "variant",
            )

    if link is None:
        copy()
        assert target.read_bytes() == original.read_bytes()
    else:
        with pytest.raises(ValueError, match="Symlinked"):
            copy()
        if link == "target_file":
            assert payload.read_bytes() == b"preserve existing content"


if __name__ == "__main__":
    unittest.main()
