#!/usr/bin/env python3
"""Create an auditable derived run by replacing one provider with its clean retest."""

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
    parser.add_argument("base", type=Path)
    parser.add_argument("retest", type=Path)
    parser.add_argument("provider")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"output already exists: {args.output}")
    base_records = load(args.base)
    retest_records = load(args.retest)
    if not retest_records or {row["provider"] for row in retest_records} != {args.provider}:
        raise SystemExit("retest must contain exactly the requested provider")
    base_keys = {(row["case_id"], row["run"]) for row in base_records if row["provider"] == args.provider}
    retest_by_key = {(row["case_id"], row["run"]): row for row in retest_records}
    if base_keys != set(retest_by_key):
        raise SystemExit("base and retest case/run keys differ")

    shutil.copytree(args.base, args.output)
    target_artifacts = args.output / "artifacts" / args.provider
    if target_artifacts.exists():
        shutil.rmtree(target_artifacts)
    shutil.copytree(args.retest / "artifacts" / args.provider, target_artifacts)
    merged = [
        retest_by_key[(row["case_id"], row["run"])] if row["provider"] == args.provider else row
        for row in base_records
    ]
    (args.output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged), encoding="utf-8"
    )
    manifest_path = args.output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retest_manifest = json.loads((args.retest / "manifest.json").read_text(encoding="utf-8"))
    manifest["derived_from"] = {
        "base_run": args.base.as_posix(),
        "provider_retests": {
            args.provider: {
                "path": args.retest.as_posix(),
                "started_at": retest_manifest.get("started_at"),
                "completed_at": retest_manifest.get("completed_at"),
            }
        },
        "reason": "replace account-rate-limited calls with a separately preserved paced retest",
    }
    replacement = next(item for item in retest_manifest["providers"] if item["name"] == args.provider)
    manifest["providers"] = [replacement if item["name"] == args.provider else item for item in manifest["providers"]]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created {args.output} with {len(merged)} records")


if __name__ == "__main__":
    main()
