"""Materialize archived product inputs from the shared immutable dataset cache."""

from __future__ import annotations

import gzip
import tempfile
from pathlib import Path

from harness.benchmarks.data import binding, definition_path
from harness.record_io import bytes_sha256, read_json


def verify_distribution(manifest, pin):
    """Accept only the exact runtime inventory from a recorded HF edition."""
    selection = pin.get("distribution")
    if not selection:
        raise ValueError("Pinned dataset inventory changed")
    revision = manifest.get("distribution_revision")
    if not revision:
        raise ValueError("Public dataset requires an immutable distribution revision")
    selected = binding(selection["track"], revision)
    expected = read_json(definition_path(selection["track"], selection["manifest"], revision))
    if manifest != {**expected, "distribution_revision": selected["revision"]}:
        raise ValueError("Public dataset runtime inventory changed")


def materialize(home: Path, pin):
    """Prepare a new input directory without downloading scores or provider outputs."""
    selection = pin["distribution"]
    selected = binding(selection["track"])
    revision = selected["revision"]
    manifest = read_json(definition_path(selection["track"], selection["manifest"], revision))
    if manifest["commit"] != pin["commit"] or manifest["cases"] != pin["cases"]:
        raise ValueError("Public dataset differs from the selected upstream cohort")
    if set(manifest["source_files"]) != set(manifest["files"]) or any(
        manifest["files"].get(name, {}).get("sha256") != expected
        for name, expected in pin.get("files", {}).items()
    ):
        raise ValueError("Public dataset differs from the pinned source files")
    home.mkdir(parents=True, exist_ok=True)
    target = home / "dataset"
    if target.exists():
        raise ValueError("Input directory already exists")
    with tempfile.TemporaryDirectory(prefix=".dataset-", dir=home) as temporary:
        stage = Path(temporary) / "dataset"
        stage.mkdir()
        for name, expected in manifest["files"].items():
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("Unsafe runtime input path")
            source = manifest["source_files"][name]
            raw = definition_path(selection["track"], source["path"], revision).read_bytes()
            if source.get("compression") == "gzip":
                raw = gzip.decompress(raw)
            elif source.get("compression") is not None:
                raise ValueError("Unknown archived input compression")
            if bytes_sha256(raw) != expected["sha256"] or len(raw) != expected["bytes"]:
                raise ValueError("Public dataset materialization changed: " + name)
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        from harness.web_scraper.pipeline import save_json

        save_json(stage / "manifest.json", {**manifest, "distribution_revision": revision})
        stage.rename(target)
