"""Hash-verified, installed-package benchmark inputs."""

import hashlib
import json
from pathlib import Path

from harness.benchmarks.data import binding, cache_home

DEFINITIONS = cache_home() / "websearch" / binding("websearch")["revision"] / "definitions"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def definition_path(name):
    from harness.benchmarks.data import definition_path as resolve
    return resolve("websearch", name)


def registry():
    return json.loads(definition_path("registry.json").read_bytes())


def load_dataset(identity):
    binding = registry()["datasets"][identity]
    from harness.benchmarks.data import binding as snapshot_binding, definition_path as snapshot_file
    source = snapshot_binding("websearch").get("dataset_sources", {}).get(identity)
    path = snapshot_file(source, binding["path"]) if source else definition_path(binding["path"])
    raw = path.read_bytes()
    if digest(raw) != binding["sha256"]:
        raise ValueError("Benchmark dataset hash mismatch: " + identity)
    dataset = json.loads(raw)
    if dataset["id"] != identity or len(dataset["cases"]) != binding["count"]:
        raise ValueError("Benchmark dataset identity or count mismatch")
    from .contracts import validate_cases

    validate_cases(dataset["cases"])
    return dataset


def resolve_profile(profile):
    """Expand explicit references; retain historical standalone profiles unchanged."""
    profile = dict(profile)
    for field in ("cases", "reference_checks", "reference_artifacts"):
        value = profile.get(field)
        if isinstance(value, dict) and "benchmark_datasets" in value:
            if (
                field != "cases"
                or value.get("field") != field
                or not value["benchmark_datasets"]
            ):
                raise ValueError("Benchmark composition must name case datasets")
            combined = []
            for binding in value["benchmark_datasets"]:
                identity = binding["id"]
                if binding.get("sha256") != registry()["datasets"][identity]["sha256"]:
                    raise ValueError("Benchmark field binding mismatch")
                combined.extend(load_dataset(identity)[field])
            if len({case["id"] for case in combined}) != len(combined):
                raise ValueError("Benchmark composition contains duplicate cases")
            profile[field] = combined
            continue
        if isinstance(value, dict) and "benchmark_dataset" in value:
            identity = value["benchmark_dataset"]
            if (
                value.get("field") != field
                or value.get("sha256") != registry()["datasets"][identity]["sha256"]
            ):
                raise ValueError("Benchmark field binding mismatch")
            profile[field] = load_dataset(identity)[field]
    return profile


def implementation_sources():
    return {p.name: p for p in sorted(Path(__file__).parent.glob("*.py"))}
