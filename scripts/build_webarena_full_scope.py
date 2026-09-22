"""Freeze the complete official WebArena-Verified task order as a separate split."""

from __future__ import annotations

import argparse
import hashlib
import importlib.resources
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_scope(official_path: Path, sample_path: Path) -> dict:
    official = json.loads(official_path.read_text(encoding="utf-8"))
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    if not isinstance(official, list) or not official:
        raise ValueError("official WebArena-Verified dataset must be a nonempty array")
    ids = [row.get("task_id") for row in official]
    if any(type(task_id) is not int for task_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("official task IDs must be unique integers")
    if ids != list(range(len(ids))):
        raise ValueError("official task order must match ascending contiguous task IDs")
    sample_ids = [row.get("task_id") for row in sample.get("tasks") or []]
    if (
        sample.get("schema") != "lex_webarena_verified_pilot/1"
        or any(type(task_id) is not int for task_id in sample_ids)
        or len(set(sample_ids)) != len(sample_ids)
        or not set(sample_ids) <= set(ids)
    ):
        raise ValueError("the independent sample split is invalid")
    return {
        "schema": "lex_webarena_verified_full_scope/1",
        "benchmark": "WebArena-Verified",
        "official_dataset_sha256": _sha256(official_path),
        "task_count": len(ids),
        "task_ids": ids,
        "execution_order": "official_dataset_order",
        "browser": "chromium",
        "success_policy": "one verified official-evaluator pass per case",
        "retry_policy": "never repeat a passed case; repair failures after the initial sweep",
        "independent_sample_split": {
            "path": "evaluation/trajectory-replay/webarena-verified/selection.json",
            "sha256": _sha256(sample_path),
            "task_count": len(sample_ids),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-dataset", type=Path)
    parser.add_argument(
        "--sample-selection",
        type=Path,
        default=Path("evaluation/trajectory-replay/webarena-verified/selection.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    official = args.official_dataset or Path(
        str(
            importlib.resources.files("webarena_verified").joinpath(
                "assets/dataset/webarena-verified.json"
            )
        )
    )
    scope = build_scope(official, args.sample_selection)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"task_count": scope["task_count"], "sha256": _sha256(args.output)}))


if __name__ == "__main__":
    main()
