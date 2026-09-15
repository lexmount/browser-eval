#!/usr/bin/env python3
"""Clone reviewed evidence while replacing only its canonical reference tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from assemble_popular_sites_two_method_stage import copy_canonical_reference_tree


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists():
        parser.error("Output exists; refusing to overwrite evidence")
    output.mkdir(parents=True)
    for child in source.iterdir():
        if child.name in {"sources", "list-references"}:
            continue
        target = output / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)
    dataset = json.loads((output / "dataset.json").read_text())
    copy_canonical_reference_tree(dataset, output)
    for name in ("dataset.json", "results.jsonl", "result-review.json"):
        if digest(source / name) != digest(output / name):
            raise ValueError(f"Core reviewed evidence changed: {name}")
    print(
        json.dumps(
            {
                "source": str(source),
                "output": str(output),
                "dataset_sha256": digest(output / "dataset.json"),
                "results_sha256": digest(output / "results.jsonl"),
                "review_sha256": digest(output / "result-review.json"),
                "provider_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
