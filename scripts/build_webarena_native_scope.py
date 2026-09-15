"""Pre-register tasks runnable on one native Docker architecture."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

MAX_TASKS_PER_QUALIFICATION_SHARD = 12
DEFAULT_EVALUATOR_EXCLUSIONS = Path(
    "evaluation/browser-use-public/webarena-evaluator-exclusions.json"
)


def build_scope(
    suite_path: Path,
    image_lock_path: Path,
    architecture: str,
    evaluator_exclusions_path: Path | None = DEFAULT_EVALUATOR_EXCLUSIONS,
) -> dict:
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    image_lock = json.loads(image_lock_path.read_text(encoding="utf-8"))
    evaluator_exclusions: dict[str, dict] = {}
    if evaluator_exclusions_path is not None:
        payload = json.loads(evaluator_exclusions_path.read_text(encoding="utf-8"))
        if payload.get("schema") != "lex_webarena_evaluator_exclusions/1":
            raise ValueError("WebArena evaluator exclusions schema is invalid")
        rows = payload.get("excluded_tasks") or []
        evaluator_exclusions = {str(row["task_id"]): row for row in rows}
        if len(evaluator_exclusions) != len(rows):
            raise ValueError("WebArena evaluator exclusions contain duplicate task IDs")
        suite_ids = {str(task["id"]) for task in suite["tasks"]}
        unknown = sorted(set(evaluator_exclusions).difference(suite_ids))
        if unknown:
            raise ValueError(f"WebArena evaluator exclusions contain unknown tasks: {unknown}")
        for task_id, row in evaluator_exclusions.items():
            if not str(row.get("reason") or "").strip() or not str(
                row.get("evidence_id") or ""
            ).strip():
                raise ValueError(f"WebArena evaluator exclusion {task_id} lacks evidence")
    site_architectures = {
        site: str(row["architecture"]) for site, row in image_lock["images"].items()
    }
    included: list[str] = []
    batch_tasks: dict[tuple[str, ...], list[str]] = defaultdict(list)
    excluded: list[dict] = []
    for task in suite["tasks"]:
        sites = [str(site) for site in task["environment"]["sites"]]
        incompatible = [site for site in sites if site_architectures[site] != architecture]
        evaluator_exclusion = evaluator_exclusions.get(str(task["id"]))
        if evaluator_exclusion is not None:
            excluded.append(
                {
                    "task_id": task["id"],
                    "sites": sites,
                    "incompatible_sites": [],
                    "exclusion_kind": "confirmed_upstream_evaluator_defect",
                    "reason": str(evaluator_exclusion["reason"]),
                    "evidence_id": str(evaluator_exclusion["evidence_id"]),
                }
            )
        elif incompatible:
            excluded.append(
                {
                    "task_id": task["id"],
                    "sites": sites,
                    "incompatible_sites": incompatible,
                    "exclusion_kind": "non_native_site_image",
                    "reason": (
                        f"official site image is not native {architecture}; "
                        "cross-architecture emulation is excluded"
                    ),
                }
            )
        else:
            task_id = str(task["id"])
            included.append(task_id)
            batch_tasks[tuple(sorted(sites))].append(task_id)
    return {
        "schema": "lex_browser_use_native_scope/1",
        "architecture": architecture,
        "selection_stage": "before_trajectory_qualification_and_browser_measurement",
        "policy": {
            "engine_independent": True,
            "native_site_images_only": True,
            "post_result_deletion_forbidden": True,
            "qualification_shard_algorithm": "stable_round_robin",
            "max_tasks_per_qualification_shard": MAX_TASKS_PER_QUALIFICATION_SHARD,
        },
        "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        "image_lock_sha256": hashlib.sha256(image_lock_path.read_bytes()).hexdigest(),
        "evaluator_exclusions_sha256": (
            hashlib.sha256(evaluator_exclusions_path.read_bytes()).hexdigest()
            if evaluator_exclusions_path is not None
            else None
        ),
        "candidate_count": len(suite["tasks"]),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "included_task_ids": included,
        "qualification_batches": [
            {
                "id": "+".join(sites),
                "sites": list(sites),
                "task_count": len(task_ids),
                "shard_count": (
                    len(task_ids) + MAX_TASKS_PER_QUALIFICATION_SHARD - 1
                )
                // MAX_TASKS_PER_QUALIFICATION_SHARD,
                "task_ids": task_ids,
            }
            for sites, task_ids in sorted(batch_tasks.items())
        ],
        "excluded_tasks": excluded,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-suite.json"),
    )
    parser.add_argument(
        "--image-lock",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-environment-images.json"),
    )
    parser.add_argument("--architecture", default="amd64")
    parser.add_argument(
        "--evaluator-exclusions",
        type=Path,
        default=DEFAULT_EVALUATOR_EXCLUSIONS,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_scope(
        args.suite,
        args.image_lock,
        args.architecture,
        args.evaluator_exclusions,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
