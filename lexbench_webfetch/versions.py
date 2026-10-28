"""Freeze benchmark editions and classify measurement compatibility."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .benchmark import public_task
from .scoring import SCORING_SOURCE_SHA256


def _digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise ValueError("Release version must be an explicit MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in value.split("."))


def _measurement(dataset: dict) -> dict:
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Release dataset needs nonempty cases")
    ids = [case.get("id") for case in cases]
    if (any(not isinstance(key, str) or not key for key in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("Release dataset requires unique nonempty case IDs")
    inputs, contracts = [], []
    for case in sorted(cases, key=lambda item: item["id"]):
        task = public_task(case)
        request = {key: task[key] for key in (
            "id", "url", "track", "task_kind", "retrieval_hints", "evaluation_url",
        ) if key in task}
        request["prompt"] = task.get("task_prompt") or task.get("description", "")
        route = task.get("source_benchmark", {})
        request["source_route"] = {key: route[key] for key in
                                   ("mode", "hosts", "entry_path") if key in route}
        mime = task.get("source_verification", {}).get("mime")
        if mime is not None:
            request["input_mime"] = mime
        inputs.append(request)
        ignored = {"name", "category", "description", "task_prompt", "source_review_url",
                   "url", "track", "task_kind", "retrieval_hints", "evaluation_url"}
        contract = {key: value for key, value in task.items() if key not in ignored}
        for key in ("ground_truth_main_content", "reference_answer"):
            if case.get(key) is not None:
                contract[key + "_sha256"] = _digest(case[key])
        contracts.append(contract)
    return {"case_count": len(cases), "case_ids_sha256": _digest(sorted(ids)),
            "input_contract_sha256": _digest(inputs),
            "acceptance_sha256": _digest(contracts)}


def _validate(release: dict) -> tuple[int, int, int]:
    if release.get("schema_version") != 1 or release.get("kind") != "benchmark_release":
        raise ValueError("Unsupported benchmark release")
    benchmark = release.get("benchmark")
    if not isinstance(benchmark, str) or not re.fullmatch(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", benchmark):
        raise ValueError("Release benchmark needs a stable identifier")
    version = _version(release.get("version"))
    if type(release.get("case_count")) is not int or release["case_count"] < 1:
        raise ValueError("Release case count must be positive")
    for key in ("dataset_sha256", "case_ids_sha256", "input_contract_sha256",
                "acceptance_sha256", "scoring_source_sha256"):
        if not isinstance(release.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", release[key]):
            raise ValueError("Release needs an immutable SHA-256: " + key)
    revision = release.get("scorer_revision")
    if revision is not None and (not isinstance(revision, str)
                                 or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ValueError("Scorer revision must be a full immutable Git commit")
    return version


def create_release(dataset: dict, *, benchmark: str, version: str,
                   previous: dict | None = None, change_note: str | None = None,
                   scoring_source_sha256: str = SCORING_SOURCE_SHA256,
                   scorer_revision: str | None = None) -> dict:
    """Identify an edition; source qualification and correction review stay upstream."""
    release = {"schema_version": 1, "kind": "benchmark_release",
               "benchmark": benchmark, "version": version,
               "dataset_sha256": _digest(dataset), **_measurement(dataset),
               "scoring_source_sha256": scoring_source_sha256}
    if scorer_revision is not None:
        release["scorer_revision"] = scorer_revision
    new = _validate(release)
    if previous is not None:
        old = _validate(previous)
        if benchmark != previous["benchmark"] or new <= old:
            raise ValueError("A successor needs the same benchmark and a newer version")
        if new[0] == old[0]:
            if any(release[key] != previous[key] for key in (
                "case_count", "case_ids_sha256", "input_contract_sha256",
            )):
                raise ValueError("Changed task population or input contract requires a major version")
            changed = any(release[key] != previous[key] for key in (
                "acceptance_sha256", "scoring_source_sha256",
            ))
            if changed and new[1] == old[1]:
                raise ValueError("Changed acceptance or scoring requires a minor version")
            if changed and (not isinstance(change_note, str) or not change_note.strip()):
                raise ValueError("A scoring correction requires a change note")
        release["previous_release_sha256"] = _digest(previous)
    if change_note is not None:
        if not isinstance(change_note, str) or not change_note.strip():
            raise ValueError("Release change note must be nonempty")
        release["change_note"] = change_note.strip()
    return release


def verify_release(release: dict, dataset: dict, *,
                   scoring_source_sha256: str | None = None,
                   exact_dataset: bool = True) -> None:
    """Verify a frozen input, or the same contract in a report's presentation wrapper."""
    _validate(release)
    actual = _measurement(dataset)
    if any(release[key] != value for key, value in actual.items()):
        raise ValueError("Release dataset measurement contract changed")
    if exact_dataset and release["dataset_sha256"] != _digest(dataset):
        raise ValueError("Release dataset bytes or metadata changed")
    if scoring_source_sha256 is not None and release["scoring_source_sha256"] != scoring_source_sha256:
        raise ValueError("Release scoring source differs from the selected evaluator")


def compare_releases(left: dict, right: dict) -> dict:
    """Classify dataset/rule compatibility; execution controls remain report-owned."""
    a, b = _validate(left), _validate(right)
    fields = ("case_ids_sha256", "input_contract_sha256", "acceptance_sha256",
              "scoring_source_sha256")
    differences = [key for key in fields if left[key] != right[key]]
    if left["benchmark"] != right["benchmark"] or a[0] != b[0]:
        mode, reason = "separate", "Different benchmark or major edition"
    elif left["version"] == right["version"] and differences:
        raise ValueError("Release version reused for a different measurement contract")
    elif any(key in differences for key in ("case_ids_sha256", "input_contract_sha256")):
        raise ValueError("Task population or inputs changed within one major version")
    elif differences:
        if a[:2] == b[:2]:
            raise ValueError("Acceptance or scoring changed within one minor version")
        mode, reason = "rescore", "Recompute both saved cohorts with one acceptance and scoring edition"
    else:
        mode, reason = "direct", "Same task population, input contract, acceptance and scorer"
    return {"mode": mode, "reason": reason, "differences": differences}


def freeze_release(dataset_path: Path, output: Path, **options) -> dict:
    """Write one immutable sidecar beside external benchmark inputs."""
    if dataset_path.is_symlink():
        raise ValueError("Release dataset rejects symlinks")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    release = create_release(dataset, **options)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(release, ensure_ascii=False, indent=2) + "\n")
    return release


def catalog_release(path: Path | None = None, *, require_current_scorer: bool = True) -> dict:
    """Read the published catalog edition and verify its complete suite bindings."""
    from .benchmark import SUITES, registry, validate

    selected = path if path is not None else SUITES / "benchmark-release.json"
    if selected.is_symlink():
        raise ValueError("Catalog release rejects symlinks")
    release = json.loads(selected.read_text())
    if release.get("schema_version") != 1 or release.get("kind") != "benchmark_catalog_release":
        raise ValueError("Unsupported benchmark catalog release")
    _version(release.get("version"))
    if release.get("tag") != "bench-v" + release["version"]:
        raise ValueError("Catalog release tag differs from its version")
    if not re.fullmatch(r"[0-9a-f]{64}", release.get("scoring_source_sha256", "")):
        raise ValueError("Catalog release needs a scoring source SHA-256")
    if require_current_scorer and release["scoring_source_sha256"] != SCORING_SOURCE_SHA256:
        raise ValueError("Catalog release scoring source changed")
    revision = release.get("scorer_revision", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Catalog release needs a full scorer commit")
    actual = []
    for entry in registry()["suites"]:
        validated = validate(entry["id"])
        actual.append({**validated, "dataset_id": entry["dataset_id"],
                       "directory": entry["directory"], "files": entry["files"]})
    if release.get("suites") != actual or release.get("case_count") != sum(
            row["case_count"] for row in actual):
        raise ValueError("Catalog release task or frozen reference bindings changed")
    return release


def installed_version() -> dict:
    """Identify development scorers without assigning them a published edition."""
    release = catalog_release(require_current_scorer=False)
    if release["scoring_source_sha256"] == SCORING_SOURCE_SHA256:
        return release
    return {"status": "unreleased", "version": None, "tag": None,
            "scoring_source_sha256": SCORING_SOURCE_SHA256,
            "published_catalog": release}
