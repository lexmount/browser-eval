"""Group unresolved full-split cases by official template and observed failure."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signature(row: dict | None) -> str:
    if row is None:
        return "missing_trajectory"
    status = row.get("status")
    if status in {"needs_trajectory", "needs_response_builder"}:
        return status
    error = str(row.get("error") or "")
    if "tab " in error and " did not become available" in error:
        return "missing_browser_tab"
    if "Locator.wait_for" in error:
        match = re.search(r"waiting for (.+)", error)
        target = match.group(1) if match else "unknown_locator"
        return "locator_timeout: " + target[:180]
    if error:
        lines = [line.strip() for line in error.splitlines() if line.strip()]
        return "runtime_error: " + lines[-1][:180]
    return "official_evaluator_failure"


def group_failures(official_path: Path, ledger_path: Path, progress_paths: list[Path]) -> dict:
    official = json.loads(official_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ids = [row["task_id"] for row in official]
    if (
        ids != list(range(len(ids)))
        or ledger.get("schema") != "lex_webarena_verified_full_ledger/1"
        or [row.get("task_id") for row in ledger.get("rows") or []] != ids
        or not progress_paths
    ):
        raise ValueError("failure grouping requires a complete audited official scope")
    latest: dict[int, dict] = {}
    for progress_path in progress_paths:
        rows = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]
        if [row.get("task_id") for row in rows] != ids:
            raise ValueError("every sweep must contain all cases in official order")
        for row in rows:
            if (
                row.get("action_count") is not None
                or row.get("error")
                or row.get("status") in {"needs_trajectory", "needs_response_builder"}
            ):
                latest[row["task_id"]] = row
    groups: dict[tuple[int, str], list[int]] = defaultdict(list)
    for definition, effective in zip(official, ledger["rows"], strict=True):
        if effective["status"] == "pass":
            continue
        task_id = definition["task_id"]
        key = (definition["intent_template_id"], _signature(latest.get(task_id)))
        groups[key].append(task_id)
    grouped = [
        {
            "intent_template_id": template_id,
            "failure_signature": signature,
            "count": len(task_ids),
            "task_ids": task_ids,
        }
        for (template_id, signature), task_ids in groups.items()
    ]
    grouped.sort(key=lambda row: (-row["count"], row["intent_template_id"], row["task_ids"]))
    return {
        "schema": "lex_webarena_verified_failure_groups/1",
        "official_dataset_sha256": _sha256(official_path),
        "ledger_sha256": _sha256(ledger_path),
        "sweep_progress_sha256": [_sha256(path) for path in progress_paths],
        "total": len(ids),
        "unresolved": sum(row["count"] for row in grouped),
        "groups": grouped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-dataset", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--progress", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = group_failures(args.official_dataset, args.ledger, args.progress)
    if args.output.exists():
        raise ValueError("failure grouping output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"unresolved": result["unresolved"], "groups": len(result["groups"])}))


if __name__ == "__main__":
    main()
