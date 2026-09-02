#!/usr/bin/env python3
"""Create an auditable run by overlaying selected provider/case attempts."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("overlays", nargs="+", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"output already exists: {args.output}")
    base_records = load(args.base)
    valid_keys = {(item["provider"], item["case_id"], item["run"]) for item in base_records}
    replacements: dict[tuple[str, str, int], tuple[dict, Path]] = {}
    for overlay in args.overlays:
        for item in load(overlay):
            key = (item["provider"], item["case_id"], item["run"])
            if key not in valid_keys:
                raise SystemExit(f"overlay key absent from base: {key}")
            replacements[key] = (item, overlay)
    shutil.copytree(args.base, args.output)
    merged = []
    for item in base_records:
        key = (item["provider"], item["case_id"], item["run"])
        replacement = replacements.get(key)
        if replacement:
            item, source_run = replacement
            source = source_run / item["artifact_path"]
            target = args.output / item["artifact_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        merged.append(item)
    (args.output / "results.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in merged), encoding="utf-8")
    manifest_path = args.output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["derived_from"] = {"base_run": args.base.as_posix(), "overlays": [item.as_posix() for item in args.overlays], "replacement_count": len(replacements), "reason": "replace invalid runtime attempts and corrected ground-truth URL attempts while retaining every source run"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created {args.output}; replaced {len(replacements)} attempts")


if __name__ == "__main__":
    main()
