"""Hash-verified, installed-package benchmark inputs."""

import hashlib
import json
from pathlib import Path

def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def definition_path(name, *, revision=None):
    from harness.benchmarks.data import definition_path as resolve
    return resolve("websearch", name, revision)


def registry(*, revision=None):
    return json.loads(definition_path("registry.json", revision=revision).read_bytes())


def load_dataset(identity, *, revision=None):
    binding = registry(revision=revision)["datasets"][identity]
    from harness.benchmarks.data import binding as snapshot_binding, definition_path as snapshot_file
    selected = snapshot_binding("websearch", revision)
    source = selected.get("dataset_sources", {}).get(identity)
    if revision is not None and "definitions/" + binding["path"] in selected["files"]:
        path = definition_path(binding["path"], revision=revision)
    elif source:
        source_revision = selected.get("external_sources", {}).get(source, {}).get("revision")
        if revision is not None and source_revision is None:
            raise ValueError("Historical delegated dataset requires its recorded source context")
        path = snapshot_file(source, binding["path"], source_revision)
    else:
        path = definition_path(binding["path"], revision=revision)
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
