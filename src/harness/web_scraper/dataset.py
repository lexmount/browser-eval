"""Materialize archived product inputs from the shared immutable dataset cache."""

from __future__ import annotations

import gzip
import importlib
import tempfile
from pathlib import Path

from harness.benchmarks.data import binding, definition_path
from harness.record_io import bytes_sha256, read_json

BENCHMARKS = {
    "zyte": {
        "profile": "zyte-product-extraction.json",
        "module": None,
        "snapshot_project": "browser-eval-zyte140-snapshots",
        "snapshot_distribution": {
            "repository": "Lexmount/LexBench-Zyte-Product-Extraction",
            "input_path": "definitions/runtime/manifest.json",
            "input_format": "html-manifest",
        },
    },
    "swde": {
        "profile": "swde.json",
        "module": "harness.web_scraper.swde",
        "snapshot_project": "browser-eval-swde800-snapshots",
        "snapshot_distribution": {
            "repository": "Lexmount/LexBench-SWDE",
            "input_path": "inputs.jsonl",
            "input_format": "html-jsonl",
        },
    },
}


def benchmark_config(home=None, *, name=None):
    """Resolve preparation, schemas and scoring from one benchmark registry."""
    if name is None:
        name = read_json(Path(home) / "dataset/manifest.json").get("benchmark", "zyte")
    if name not in BENCHMARKS:
        raise ValueError("Unknown extraction benchmark: " + str(name))
    item = BENCHMARKS[name]
    return {
        "profile": Path(__file__).resolve().parents[1] / "benchmarks/cards" / item["profile"],
        "adapter": importlib.import_module(item["module"]) if item["module"] else None,
        "snapshot_project": item.get("snapshot_project"),
        "snapshot_distribution": item.get("snapshot_distribution"),
    }


def require_product_reference(home):
    """Keep product-only report and review entries out of other schema families."""
    if benchmark_config(home)["adapter"] is not None:
        raise ValueError("This interface requires product references; use collection and scoring.")


def adapter_sha256(home):
    adapter = benchmark_config(home)["adapter"]
    return bytes_sha256(Path(adapter.__file__).read_bytes()) if adapter else None


def registry_sha256():
    return bytes_sha256(Path(__file__).read_bytes())


def task_bindings(home):
    config = benchmark_config(home)
    if config["adapter"] is None:
        return {}
    return {
        "task_adapter_sha256": adapter_sha256(home),
        "dataset_profile_sha256": bytes_sha256(config["profile"].read_bytes()),
        "dataset_registry_sha256": registry_sha256(),
    }


def distribution_identity(home: Path, pin, selection):
    """Link a published dataset only after matching the report's frozen inputs."""
    if selection["track"] != "web-scraper" or not selection.get("revision"):
        raise ValueError("Report dataset distribution requires a pinned Web Scraper edition")
    selected = binding(selection["track"], selection["revision"])
    manifest = read_json(
        definition_path(selection["track"], "runtime/manifest.json", selection["revision"])
    )
    if manifest["commit"] != pin["commit"] or manifest["cases"] != pin["cases"]:
        raise ValueError("Report dataset distribution belongs to different upstream inputs")
    html = {name for name in manifest["files"] if name.startswith("html/")}
    if len(html) != pin["cases"]:
        raise ValueError("Report dataset distribution has an incomplete HTML population")
    for name in html | set(pin.get("files", {})):
        expected = manifest["files"].get(name, {}).get("sha256")
        if expected != bytes_sha256((home / "dataset" / name).read_bytes()):
            raise ValueError("Report dataset distribution differs from frozen input: " + name)
    return {
        "track": selection["track"],
        "repository": selected["repository"],
        "revision": selected["revision"],
        "archive_sha256": selected["archive"]["sha256"],
        "url": "https://huggingface.co/datasets/"
        + selected["repository"]
        + "/tree/"
        + selected["revision"],
    }


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
    from harness.web_scraper.pipeline import require_external_output, save_json

    require_external_output(home)
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
        save_json(stage / "manifest.json", {**manifest, "distribution_revision": revision})
        stage.rename(target)
