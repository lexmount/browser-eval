#!/usr/bin/env python3
"""Assemble complete, disjoint provider runs into one immutable evidence view."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.runner import summarize
from harness.web_fetch.safety import contained_path


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines() if line]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()} if root.is_dir() else {}


def sanitized_batch(manifest: dict, source_hash: str, provider: str) -> dict:
    result = deepcopy(manifest)
    result["dataset"]["source"] = "evaluation/popular-sites-dataset.json"
    result["provider_config_source"] = "configs/providers.resources.json"
    result.get("environment", {}).pop("hostname", None)
    result["assembly_source"] = {
        "provider": provider,
        "manifest_sha256": source_hash,
        "selection": "complete provider cohort; no score-based selection",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--canonical-dataset", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"output already exists: {args.output}")

    manifests = [json.loads((path / "manifest.json").read_text(encoding="utf-8")) for path in args.runs]
    datasets = [json.loads((path / "dataset.json").read_text(encoding="utf-8")) for path in args.runs]
    run_canonical = datasets[0]
    canonical = run_canonical
    if args.canonical_dataset:
        frozen = json.loads(args.canonical_dataset.read_text(encoding="utf-8"))
        for key in ("schema_version", "id", "tool", "dynamic_scoring", "cases"):
            if frozen.get(key) != run_canonical.get(key):
                raise SystemExit(f"canonical dataset mismatch: {key}")
        frozen_comparison = frozen.get("comparison", {})
        run_comparison = run_canonical.get("comparison", {})
        binding_fields = {"reference_binding_mode", "unverified_reference_cases"}
        if {
            key: value for key, value in frozen_comparison.items() if key not in binding_fields
        } != {
            key: value for key, value in run_comparison.items() if key not in binding_fields
        }:
            raise SystemExit("canonical dataset mismatch: comparison")
        canonical = frozen
    canonical_cases = canonical.get("cases", [])
    case_ids = [case.get("id") for case in canonical_cases]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise SystemExit("canonical run has empty or duplicate case IDs")
    for dataset in datasets[1:]:
        for key in ("schema_version", "id", "tool", "dynamic_scoring", "comparison", "cases"):
            if dataset.get(key) != run_canonical.get(key):
                raise SystemExit(f"dataset mismatch: {key}")
    for folder in ("sources", "list-references"):
        expected = tree_bytes(args.runs[0] / folder)
        for path in args.runs[1:]:
            if tree_bytes(path / folder) != expected:
                raise SystemExit(f"frozen input tree mismatch: {folder}")

    by_provider: dict[str, tuple[Path, list[dict], dict, dict]] = {}
    attempt_ids: set[str] = set()
    for path, manifest in zip(args.runs, manifests):
        records = load(path)
        names = {item.get("provider") for item in records}
        if len(names) != 1:
            raise SystemExit(f"run must contain exactly one provider: {path}")
        provider = names.pop()
        if provider in by_provider:
            raise SystemExit(f"duplicate provider run is not selectable: {provider}")
        keys = [(item.get("case_id"), item.get("run")) for item in records]
        expected_keys = [(case_id, 1) for case_id in case_ids]
        if sorted(keys) != sorted(expected_keys):
            raise SystemExit(f"incomplete provider run: {provider}")
        current_ids = {item.get("attempt_id") for item in records}
        if None in current_ids or len(current_ids) != len(records) or attempt_ids & current_ids:
            raise SystemExit(f"duplicate or missing attempt ID: {provider}")
        attempt_ids.update(current_ids)
        infos = [item for item in manifest.get("providers", []) if item.get("name") == provider]
        if len(infos) != 1:
            raise SystemExit(f"provider identity missing or ambiguous: {provider}")
        for item in records:
            artifact = contained_path(path, item["artifact_path"])
            if digest(artifact) != item.get("metrics", {}).get("content_sha256"):
                raise SystemExit(f"artifact hash mismatch: {item['attempt_id']}")
        by_provider[provider] = (path, records, infos[0], manifest)

    args.output.mkdir(parents=True)
    if args.canonical_dataset:
        canonical_root = args.canonical_dataset.parent
        scoped_roots = set(case_ids) | {canonical["id"]}
        source_root = canonical_root / "sources"
        for source in source_root.rglob("*"):
            if not source.is_file() or source.relative_to(source_root).parts[0] not in scoped_roots:
                continue
            target = args.output / "sources" / source.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        references = canonical_root / "list-references"
        for source in references.glob("*.json") if references.is_dir() else []:
            if source.stem not in case_ids:
                continue
            target = args.output / "list-references" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    else:
        for folder in ("sources", "list-references"):
            source = args.runs[0] / folder
            if source.exists():
                shutil.copytree(source, args.output / folder)
    (args.output / "artifacts").mkdir()

    merged: list[dict] = []
    provider_infos = []
    batches = []
    source_hashes = {}
    for provider, (path, records, info, manifest) in sorted(by_provider.items()):
        shutil.copytree(path / "artifacts" / provider, args.output / "artifacts" / provider)
        merged.extend(records)
        provider_infos.append(info)
        source_hash = digest(path / "manifest.json")
        source_hashes[provider] = source_hash
        batches.append(sanitized_batch(manifest, source_hash, provider))
    merged.sort(key=lambda item: (item["provider"], item["run"], case_ids.index(item["case_id"])))

    dataset = deepcopy(canonical)
    providers = sorted(by_provider)
    tracks = {case.get("track", "web") for case in canonical_cases}
    dataset["coverage_plan"] = {track: providers for track in sorted(tracks)}
    dataset_bytes = (json.dumps(dataset, ensure_ascii=False, indent=2) + "\n").encode()
    (args.output / "dataset.json").write_bytes(dataset_bytes)
    (args.output / "results.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=True) + "\n" for item in merged), encoding="utf-8")
    (args.output / "summary.json").write_text(
        json.dumps(summarize(merged), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    started = min(datetime.fromisoformat(row["started_at"]) for row in merged).isoformat()
    completed = max(datetime.fromisoformat(row["completed_at"]) for row in merged).isoformat()
    manifest = {
        "schema_version": 1,
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "tool": dataset.get("tool"),
        "started_at": started,
        "completed_at": completed,
        "providers": provider_infos,
        "attempt_count": len(merged),
        "attempt_ids": [row["attempt_id"] for row in merged],
        "unexecuted": [],
        "derived_from": {
            "source_manifest_sha256_by_provider": source_hashes,
            "reason": "assemble disjoint complete provider cohorts; no result selection",
        },
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "execution.json").write_text(
        json.dumps({"batches": batches}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created {args.output}: {len(by_provider)} providers, {len(merged)} records")


if __name__ == "__main__":
    main()
