"""Reproduce pre-qualification exclusions from hash-bound official trace ZIPs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.record_io import parse_jsonl


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _records(path: Path, name: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        matches = [value for value in archive.namelist() if value.endswith(name)]
        if len(matches) != 1:
            raise ValueError(f"{path.name}: expected exactly one {name}")
        return parse_jsonl(archive.read(matches[0]).decode())


def verify_exclusions(
    trace_dir: Path, suite_path: Path, exclusions_path: Path
) -> dict[str, Any]:
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    exclusions = json.loads(exclusions_path.read_text(encoding="utf-8"))
    if exclusions.get("schema") != "lex_webarena_evaluator_exclusions/1":
        raise ValueError("WebArena evaluator exclusions schema is invalid")
    tasks = {str(row["id"]): row for row in suite.get("tasks") or []}
    results: list[dict[str, Any]] = []
    for exclusion in exclusions.get("excluded_tasks") or []:
        task_id = str(exclusion["task_id"])
        numeric_id = task_id.removeprefix("webarena-")
        path = trace_dir / f"{numeric_id}.trace.zip"
        if not path.is_file():
            raise FileNotFoundError(f"{task_id}: official trace is missing")
        digest = _sha256(path)
        if digest != exclusion.get("source_trace_sha256"):
            raise ValueError(f"{task_id}: official trace sha256 mismatch")
        task = tasks.get(task_id)
        if task is None:
            raise ValueError(f"{task_id}: task is missing from suite")
        network = _records(path, "trace.network")
        requests = [
            row["snapshot"]["request"]
            for row in network
            if row.get("type") == "resource-snapshot"
        ]
        if task_id == "webarena-678":
            expected_events = [
                request
                for request in requests
                if parse_qs(urlsplit(str(request.get("url") or "")).query).get("namespace")
                == ["sales_order_grid"]
            ]
            passed = not expected_events
            detail = {"sales_order_grid_event_count": len(expected_events)}
        elif task_id == "webarena-760":
            route_paths = sorted(
                {
                    urlsplit(str(request.get("url") or "")).path
                    for request in requests
                    if "/route/v1/" in str(request.get("url") or "")
                }
            )
            trace_records = _records(path, "trace.trace")
            created_pages = sum(
                row.get("type") == "event"
                and row.get("class") == "Page"
                and row.get("method") == "__create__"
                for row in trace_records
            )
            declared_pages = len((task.get("environment") or {}).get("start_urls") or [])
            actions = (task.get("trajectory") or {}).get("actions") or []
            max_tab_index = max((int(row.get("tab_index") or 0) for row in actions), default=0)
            expected_origin = "-75.4716115,40.6022552"
            passed = (
                bool(route_paths)
                and all(expected_origin not in path for path in route_paths)
                and created_pages > declared_pages
                and max_tab_index >= declared_pages
            )
            detail = {
                "recorded_route_paths": route_paths,
                "expected_origin": expected_origin,
                "created_page_count": created_pages,
                "declared_start_page_count": declared_pages,
                "max_action_tab_index": max_tab_index,
            }
        elif task_id == "webarena-811":
            issue_path = "/a11yproject/a11yproject.com/-/issues/1478.json"
            matching_requests = [
                request
                for request in requests
                if urlsplit(str(request.get("url") or "")).path == issue_path
            ]
            methods = sorted(
                {str(request.get("method") or "").upper() for request in matching_requests}
            )
            expected_put_count = sum(
                str(request.get("method") or "").upper() == "PUT"
                for request in matching_requests
            )
            recorded_get_count = sum(
                str(request.get("method") or "").upper() == "GET"
                for request in matching_requests
            )
            passed = expected_put_count == 0 and recorded_get_count >= 1
            detail = {
                "issue_path": issue_path,
                "recorded_methods": methods,
                "expected_put_event_count": expected_put_count,
                "recorded_get_event_count": recorded_get_count,
            }
        else:
            raise ValueError(f"unsupported evaluator exclusion audit: {task_id}")
        results.append(
            {
                "task_id": task_id,
                "evidence_id": exclusion["evidence_id"],
                "source_trace_sha256": digest,
                "passed": passed,
                **detail,
            }
        )
    payload = {
        "schema": "lex_webarena_evaluator_exclusion_audit/1",
        "suite_sha256": _sha256(suite_path),
        "exclusions_sha256": _sha256(exclusions_path),
        "complete": bool(results) and all(row["passed"] for row in results),
        "checks": results,
    }
    if not payload["complete"]:
        failed = [row["task_id"] for row in results if not row["passed"]]
        raise RuntimeError(f"WebArena evaluator exclusions were not reproduced: {failed}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-suite.json"),
    )
    parser.add_argument(
        "--exclusions",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-evaluator-exclusions.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = verify_exclusions(args.trace_dir, args.suite, args.exclusions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
