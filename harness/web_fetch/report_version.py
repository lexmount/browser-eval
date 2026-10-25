"""Carry an explicitly selected benchmark edition through existing reports."""

import hashlib
import json
from pathlib import Path

from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.versions import verify_release

FILENAME = "benchmark-release.json"


def _input_release(root):
    execution = root / "execution.json"
    binding = (json.loads(execution.read_text()).get("aggregate", {}).get("benchmark_release")
               if execution.is_file() else None)
    manifest = root / "report-manifest.json"
    if manifest.is_file():
        declared = (json.loads(manifest.read_text()).get("benchmark_release") or {}).get("input_release")
        if declared is not None and declared != binding:
            raise ValueError("Report lost its original benchmark input release binding")
    if binding is not None:
        path = contained_path(root, binding["path"], reject_symlinks=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != binding.get("sha256"):
            raise ValueError("Measured benchmark input release changed")
    return binding


def _profile_binding(profile):
    if profile is None:
        return None
    return json.loads(Path(profile).read_text()).get("benchmark_release")


def _verify_scores(release, dataset, records):
    verify_release(release, dataset, exact_dataset=False)
    if not records or any(
        (
            row.get("metrics", {}).get("scoring_source_sha256") != release["scoring_source_sha256"]
            or row.get("scoring", {}).get("source_sha256", release["scoring_source_sha256"])
            != release["scoring_source_sha256"]
        )
        for row in records
    ):
        raise ValueError("Benchmark release differs from the saved scoring sources; rescore first")


def load_release(source, dataset, records, *, profile=None):
    """Validate the profile's edition, otherwise the measured input's edition."""
    binding = _profile_binding(profile)
    if binding is not None:
        selected = contained_path(Path(profile).parent, binding["path"], reject_symlinks=True)
    else:
        selected = contained_path(source, FILENAME, reject_symlinks=True)
        if not selected.exists():
            measured = _input_release(source)
            if measured is None:
                return None
            selected = contained_path(source, measured["path"], reject_symlinks=True)
    raw = selected.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if binding is not None and digest != binding.get("sha256"):
        raise ValueError("Profile benchmark release changed")
    release = json.loads(raw)
    _verify_scores(release, dataset, records)
    return raw, release


def bind_release(source, output, dataset, records, *, profile=None):
    """Carry the validated edition into the generated report."""
    selected = load_release(source, dataset, records, profile=profile)
    if selected is None:
        return None
    raw, release = selected
    digest = hashlib.sha256(raw).hexdigest()
    target = output / FILENAME
    target.write_bytes(raw)
    measured = _input_release(source)
    if measured is not None:
        original = contained_path(source, measured["path"], reject_symlinks=True)
        archived = contained_path(output, measured["path"], reject_symlinks=True)
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_bytes(original.read_bytes())
        execution_path = output / "execution.json"
        execution = json.loads(execution_path.read_text()) if execution_path.exists() else {}
        aggregate = execution.setdefault("aggregate", {})
        if aggregate.get("benchmark_release") != measured:
            aggregate["benchmark_release"] = measured
            execution_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2) + "\n")
    binding = {
        "path": FILENAME,
        "sha256": digest,
        "benchmark": release["benchmark"],
        "version": release["version"],
    }
    if measured is not None:
        binding["input_release"] = measured
    return binding


def check_release(root, dataset, records, *, profile=None):
    """Check the edition, rendered label and scoring identity without rescoring."""
    from bs4 import BeautifulSoup

    manifest_path = root / "report-manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    from harness.reporting.versions import check_build_version
    check_build_version(root, manifest)
    binding = manifest.get("benchmark_release")
    expected = _profile_binding(profile)
    measured = _input_release(root)
    release_path = contained_path(root, FILENAME, reject_symlinks=True)
    if binding is None:
        if expected is not None or release_path.exists() or measured is not None:
            raise ValueError("Report lost its benchmark release binding")
        return
    if binding.get("path") != FILENAME or not release_path.is_file():
        raise ValueError("Report benchmark release is missing")
    raw = release_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != binding.get("sha256") or (
        expected is not None and digest != expected.get("sha256")
    ):
        raise ValueError("Report benchmark release changed")
    release = json.loads(raw)
    _verify_scores(release, dataset, records)
    if any(binding.get(key) != release[key] for key in ("benchmark", "version")):
        raise ValueError("Report benchmark version label differs from its release")
    for route in ("index.html", "en/index.html"):
        path = root / route
        if not path.is_file():
            continue
        page = BeautifulSoup(path.read_text(), "html.parser")
        if page.select_one("[data-english-not-generated]"):
            continue
        label = page.select_one("[data-benchmark-release]")
        if (
            label is None
            or label.get("href") != ("../" if route.startswith("en/") else "") + FILENAME
            or label.get("data-benchmark-release") != release["version"]
            or label.get_text(" ", strip=True) not in (
                {"bench v" + release["version"]} if manifest.get("browser_eval") else
                {"bench v" + release["version"], release["benchmark"] + " v" + release["version"]}
            )
        ):
            raise ValueError("Report lost its benchmark version label: " + route)
