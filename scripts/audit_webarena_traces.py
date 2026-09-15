"""Audit completeness and replay portability of downloaded WebArena traces."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from harness.trajectory_replay.webarena import import_playwright_trace


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-traces.json"),
    )
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    rows = catalog.get("traces") or []
    if catalog.get("count") != 179 or len(rows) != 179:
        raise ValueError("catalog must contain exactly 179 traces")

    missing: list[str] = []
    tasks: list[dict] = []
    action_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    unmapped_counts: Counter[str] = Counter()
    recovered_input_counts: Counter[str] = Counter()
    for row in rows:
        path = args.trace_dir / row["file_name"]
        if not path.is_file():
            missing.append(row["file_name"])
            continue
        trace = import_playwright_trace(path, strict=False)
        actions = trace["actions"]
        action_counts.update(str(action["action"]) for action in actions)
        target_counts.update(
            str((action.get("target") or {}).get("by"))
            for action in actions
            if action.get("target")
        )
        unmapped_counts.update(str(call["api_name"]) for call in trace["unmapped_calls"])
        recovered_manual_inputs = sum(
            bool(action["source"].get("recovered_manual_input_from_snapshot")) for action in actions
        )
        recovered_clipboard_inputs = sum(
            bool(action["source"].get("recovered_from_input_snapshot")) for action in actions
        )
        recovered_input_counts["manual_typing"] += recovered_manual_inputs
        recovered_input_counts["clipboard_paste"] += recovered_clipboard_inputs
        tasks.append(
            {
                "task_id": row["task_id"],
                "action_count": len(actions),
                "unmapped_calls": trace["unmapped_calls"],
                "recorded_failed_actions": sum(
                    action["source"].get("recorded_status") == "fail" for action in actions
                ),
                "unresolved_external_inputs": sum(
                    bool(action["source"].get("unresolved_external_input")) for action in actions
                ),
                "recovered_manual_inputs": recovered_manual_inputs,
                "recovered_clipboard_inputs": recovered_clipboard_inputs,
            }
        )
    payload = {
        "schema": "lex_webarena_trace_audit/1",
        "expected_count": 179,
        "downloaded_count": len(tasks),
        "complete": not missing,
        "missing": missing,
        "action_counts": dict(sorted(action_counts.items())),
        "target_counts": dict(sorted(target_counts.items())),
        "unmapped_api_counts": dict(sorted(unmapped_counts.items())),
        "recovered_input_counts": dict(sorted(recovered_input_counts.items())),
        "zero_action_task_ids": [row["task_id"] for row in tasks if row["action_count"] == 0],
        "tasks": tasks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if missing:
        raise RuntimeError(f"trace download is incomplete: {len(missing)} files missing")
    if unmapped_counts:
        raise RuntimeError(f"unsupported Playwright APIs remain: {dict(unmapped_counts)}")


if __name__ == "__main__":
    main()
