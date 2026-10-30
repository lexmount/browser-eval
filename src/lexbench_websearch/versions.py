"""Freeze benchmark editions independently of package and report versions."""

import json
import re
from pathlib import Path

from .inputs import definition_path, digest, load_dataset, registry

# Provider request fields. All remaining non-editorial task fields affect grading.
_REQUEST_FIELDS = {
    "id",
    "query",
    "family",
    "language",
    "source",
    "domains",
    "intent",
    "request",
    "top_k",
    "output_scope",
    "retrieval_route",
}
_EDITORIAL_FIELDS = {
    "category",
    "difficulty",
    "origin",
    "derivation",
    "provenance_kind",
    "source_case_id",
    "source_image_sha256",
    "source_row",
    "source_sha256",
}
_HASH_FIELDS = (
    "population_sha256",
    "requests_sha256",
    "acceptance_sha256",
    "scoring_source_sha256",
)


def _hash(value):
    return digest(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise ValueError("Version must be MAJOR.MINOR.PATCH")
    return tuple(map(int, value.split(".")))


def scoring_source_sha256():
    """Bind scorer modules and their data; exclude release metadata itself."""
    from harness.benchmarks.data import binding
    package = Path(__file__).parent
    excluded = {"__init__.py", "cli.py", "versions.py"}
    sources = {
        p.name: digest(p.read_bytes())
        for p in sorted(package.glob("*.py"))
        if p.name not in excluded
    }
    sources.update(
        {
            name: item["sha256"]
            for name, item in binding("websearch")["files"].items()
            if Path(name).parent == Path("definitions")
            and (Path(name).suffix == ".txt" or Path(name).name == "contamination-patterns.json")
        }
    )
    return _hash(sources)


def _contracts(dataset):
    from .contracts import validate_cases

    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Release requires nonempty cases")
    validate_cases(cases)
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Release requires unique case IDs")
    ordered = sorted(cases, key=lambda case: case["id"])
    requests = [
        {k: v for k, v in case.items() if k in _REQUEST_FIELDS} for case in ordered
    ]
    acceptance = [
        {k: v for k, v in case.items() if k not in _REQUEST_FIELDS | _EDITORIAL_FIELDS}
        for case in ordered
    ]
    # Reference qualification and answer normalization belong to the measurement.
    extra = {k: v for k, v in dataset.items() if k not in {"id", "schema", "cases"}}
    return {
        "case_count": len(cases),
        "population_sha256": _hash(sorted(ids)),
        "requests_sha256": _hash(requests),
        "acceptance_sha256": _hash([acceptance, extra]),
    }


def _validate(release):
    if release.get("schema") != "lexbench-websearch-release/1":
        raise ValueError("Unsupported benchmark release")
    version = _version(release.get("version"))
    if release.get("tag") != "bench-v" + release["version"] and (
        release["version"],
        release.get("tag"),
    ) != ("1.0.0", "bench-1.0.0"):
        raise ValueError("Release tag must match its benchmark version")
    for key in (*_HASH_FIELDS, "dataset_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(release.get(key, ""))):
            raise ValueError("Missing immutable digest: " + key)
    if not re.fullmatch(r"[0-9a-f]{40}", str(release.get("scorer_revision", ""))):
        raise ValueError("Scorer revision must be a full Git commit")
    if type(release.get("case_count")) is not int or release["case_count"] < 1:
        raise ValueError("Release requires a positive case count")
    return version


def compare_releases(left, right):
    a, b = _validate(left), _validate(right)
    changed = [key for key in _HASH_FIELDS if left[key] != right[key]]
    if a[0] != b[0]:
        mode = "separate"
    elif any(k in changed for k in ("population_sha256", "requests_sha256")):
        raise ValueError("Changed tasks or requests require a major version")
    elif changed:
        if a[:2] == b[:2]:
            raise ValueError("Changed acceptance or scoring requires a minor version")
        mode = "rescore"
    else:
        mode = "direct"
    return {"mode": mode, "differences": changed}


def create_release(
    dataset, *, version, scorer_revision, previous=None, change_note=None
):
    release = {
        "schema": "lexbench-websearch-release/1",
        "version": version,
        "tag": "bench-v" + version,
        "dataset_sha256": _hash(dataset),
        **_contracts(dataset),
        "scoring_source_sha256": scoring_source_sha256(),
        "scorer_revision": scorer_revision,
    }
    new = _validate(release)
    if previous is not None:
        old = _validate(previous)
        if new <= old:
            raise ValueError("Successor version must increase")
        comparison = compare_releases(previous, release)
        if comparison["mode"] == "rescore" and not (
            isinstance(change_note, str) and change_note.strip()
        ):
            raise ValueError("Scoring corrections require a change note")
        release["previous_release_sha256"] = _hash(previous)
    if change_note is not None:
        if not isinstance(change_note, str) or not change_note.strip():
            raise ValueError("Change note must be nonempty")
        release["change_note"] = change_note.strip()
    return release


def verify_release(release, dataset, *, verify_scorer=True):
    _validate(release)
    expected = {
        "dataset_sha256": _hash(dataset),
        **_contracts(dataset),
    }
    if verify_scorer:
        expected["scoring_source_sha256"] = scoring_source_sha256()
    if any(release.get(key) != value for key, value in expected.items()):
        raise ValueError("Frozen dataset or scoring contract changed")


def freeze_release(dataset_path, output, **options):
    if dataset_path.is_symlink():
        raise ValueError("Dataset cannot be a symlink")
    release = create_release(json.loads(dataset_path.read_bytes()), **options)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(release, ensure_ascii=False, indent=2) + "\n")
    return release


def catalog_release(path=None, *, require_current_scorer=True):
    selected = path if path is not None else definition_path("benchmark-release.json")
    if selected.is_symlink():
        raise ValueError("Catalog cannot be a symlink")
    release = json.loads(selected.read_bytes())
    if release.get("schema") != "lexbench-websearch-catalog-release/1":
        raise ValueError("Unsupported catalog release")
    _version(release.get("version"))
    if release.get("tag") != "bench-v" + release["version"] and (
        release["version"],
        release.get("tag"),
    ) != ("1.0.0", "bench-1.0.0"):
        raise ValueError("Catalog tag must match its version")
    installed = registry()
    if release.get("current") != installed["current"] or set(
        release.get("datasets", {})
    ) != set(installed["datasets"]):
        raise ValueError("Catalog dataset inventory changed")
    for identity, binding in release["datasets"].items():
        if binding.get("version") != release["version"]:
            raise ValueError("Dataset edition differs from the catalog")
        verify_release(
            binding, load_dataset(identity), verify_scorer=require_current_scorer
        )
    if not require_current_scorer:
        current = scoring_source_sha256()
        release["development"] = {
            "scoring_status": "released"
            if all(
                b["scoring_source_sha256"] == current
                for b in release["datasets"].values()
            )
            else "unreleased",
            "scoring_source_sha256": current,
        }
    return release
