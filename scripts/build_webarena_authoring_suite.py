"""Build a warm-site, retrieve-only suite for deterministic trajectory authoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from harness.browser_use.suite import load_suite
from scripts.build_webarena_observation_suite import build_observation_suite


def build_authoring_suite(
    suite: dict[str, Any],
    qualification: dict[str, Any],
    *,
    site: str | None = None,
    target_timeout_ms: int = 5_000,
) -> dict[str, Any]:
    if target_timeout_ms < 1:
        raise ValueError("authoring target timeout must be positive")
    dispositions = {
        str(row.get("task_id")): row.get("disposition")
        for row in qualification.get("tasks") or []
    }
    selected_ids = []
    for task in suite.get("tasks") or []:
        task_id = str(task.get("id"))
        if dispositions.get(task_id) != "needs_trace_repair":
            continue
        if "retrieve" not in (task.get("tags") or []):
            continue
        sites = (task.get("environment") or {}).get("sites") or []
        if site is not None and site not in sites:
            continue
        selected_ids.append(task_id)
    if not selected_ids:
        raise ValueError("no retrieve-only trace repairs match the requested authoring scope")

    result = build_observation_suite(suite, selected_ids, output_name="authoring_page")
    for task in result["tasks"]:
        for action in task["trajectory"]["actions"]:
            if action.get("selector") is not None or isinstance(action.get("target"), dict):
                action.setdefault("timeout_ms", target_timeout_ms)
            if isinstance(action.get("target"), dict):
                action.setdefault("target_timeout_ms", target_timeout_ms)
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "trajectory_authoring": {
            "non_canonical": True,
            "diagnostic_only": True,
            "source_disposition": "needs_trace_repair",
            "task_type": "retrieve",
            "site": site,
            "task_count": len(selected_ids),
            "diagnostic_target_timeout_ms": target_timeout_ms,
            "qualification_evidence": False,
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--site")
    parser.add_argument("--target-timeout-ms", type=int, default=5_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_authoring_suite(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.qualification.read_text(encoding="utf-8")),
        site=args.site,
        target_timeout_ms=args.target_timeout_ms,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_suite(args.output)


if __name__ == "__main__":
    main()
