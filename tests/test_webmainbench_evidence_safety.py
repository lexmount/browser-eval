"""Reject untrusted artifact paths before copying WebMainBench evidence."""

import hashlib

import pytest

from harness.web_fetch.webmainbench_evidence import copy_alias_artifacts, copy_bound


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
