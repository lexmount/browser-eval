"""Build a non-canonical qualification subset from a bound correction queue."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.trajectory_replay.suite import load_suite

ALLOWED_DISPOSITIONS = {
    "eligible",
    "needs_evidence_revalidation",
    "needs_response_annotation",
    "needs_trace_repair",
}
ALLOWED_TASK_TYPES = {"retrieve", "navigate", "mutate"}


def build_subset(
    suite: dict[str, Any],
    qualification: dict[str, Any],
    *,
    dispositions: set[str],
    task_types: set[str],
    site: str | None = None,
    sites_exact: set[str] | None = None,
) -> dict[str, Any]:
    if not dispositions or not dispositions <= ALLOWED_DISPOSITIONS:
        raise ValueError("qualification subset has an unsupported disposition")
    if not task_types or not task_types <= ALLOWED_TASK_TYPES:
        raise ValueError("qualification subset has an unsupported task type")
    if site is not None and sites_exact is not None:
        raise ValueError("site and sites_exact are mutually exclusive")
    if sites_exact is not None and not sites_exact:
        raise ValueError("sites_exact must not be empty")
    disposition_by_id = {
        str(row.get("task_id")): str(row.get("disposition"))
        for row in qualification.get("tasks") or []
    }
    selected = []
    for source_task in suite.get("tasks") or []:
        task_id = str(source_task.get("id"))
        if disposition_by_id.get(task_id) not in dispositions:
            continue
        if not task_types.intersection(source_task.get("tags") or []):
            continue
        sites = (source_task.get("environment") or {}).get("sites") or []
        if site is not None and site not in sites:
            continue
        if sites_exact is not None and set(sites) != sites_exact:
            continue
        task = deepcopy(source_task)
        task.setdefault("ground_truth", {})["qualification"] = {"status": "pending"}
        selected.append(task)
    if not selected:
        raise ValueError("qualification subset is empty")

    result = deepcopy(suite)
    result["tasks"] = selected
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "qualification_subset": {
            "non_canonical": True,
            "dispositions": sorted(dispositions),
            "task_types": sorted(task_types),
            "site": site,
            "sites_exact": sorted(sites_exact) if sites_exact is not None else None,
            "task_count": len(selected),
        },
    }
    return result


def _csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--dispositions", required=True)
    parser.add_argument("--task-types", required=True)
    parser.add_argument("--site")
    parser.add_argument("--sites-exact")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_subset(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.qualification.read_text(encoding="utf-8")),
        dispositions=_csv(args.dispositions),
        task_types=_csv(args.task_types),
        site=args.site,
        sites_exact=_csv(args.sites_exact) if args.sites_exact is not None else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_suite(args.output)


if __name__ == "__main__":
    main()
