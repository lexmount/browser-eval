#!/usr/bin/env python3
"""Merge complete provider runs with identical dataset/case/run keys."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.record_io import load_jsonl


def load(path: Path) -> list[dict]:
    return load_jsonl(path / "results.jsonl", skip_blank=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("runs", nargs="+", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"output already exists: {args.output}")
    manifests = [json.loads((path / "manifest.json").read_text(encoding="utf-8")) for path in args.runs]
    dataset_ids = {item["dataset"]["id"] for item in manifests}
    if len(dataset_ids) != 1:
        raise SystemExit(f"dataset mismatch: {sorted(dataset_ids)}")
    by_provider: dict[str, tuple[Path, list[dict], dict]] = {}
    expected_keys: set[tuple[str, int]] | None = None
    for path, manifest in zip(args.runs, manifests):
        records = load(path)
        for provider in {item["provider"] for item in records}:
            selected = [item for item in records if item["provider"] == provider]
            keys = {(item["case_id"], item["run"]) for item in selected}
            if expected_keys is None:
                expected_keys = keys
            if keys != expected_keys:
                raise SystemExit(f"incomplete provider run: {provider}")
            info = next(item for item in manifest["providers"] if item["name"] == provider)
            by_provider[provider] = (path, selected, info)
    shutil.copytree(args.runs[0], args.output)
    shutil.rmtree(args.output / "artifacts")
    (args.output / "artifacts").mkdir()
    merged: list[dict] = []
    provider_infos = []
    for provider, (path, records, info) in sorted(by_provider.items()):
        shutil.copytree(path / "artifacts" / provider, args.output / "artifacts" / provider)
        merged.extend(records)
        provider_infos.append(info)
    merged.sort(key=lambda item: (item["provider"], item["run"], item["case_id"]))
    (args.output / "results.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in merged), encoding="utf-8")
    manifest = manifests[0]
    manifest["providers"] = provider_infos
    manifest["attempt_count"] = len(merged)
    manifest["derived_from"] = {"runs": [path.as_posix() for path in args.runs], "reason": "merge independently preserved complete provider runs; later runs replace duplicate providers"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created {args.output}: {len(by_provider)} providers, {len(merged)} records")


if __name__ == "__main__":
    main()
