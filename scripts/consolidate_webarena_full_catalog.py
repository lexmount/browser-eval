"""Freeze each accepted trajectory into one catalog for a fresh deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256
from scripts.run_webarena_full_catalog import (
    SCHEMA,
    TRUSTED_RECORDED_HOSTS,
    _hash,
    _load_inputs,
    _row_source_sites,
)


def _source_files(roots: list[Path], expected: set[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root in roots:
        for path in root.rglob("*.json"):
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError(f"source file is a symlink: {path}")
            if not any(word in path.name for word in ("catalog", "suite", "sites")):
                continue
            digest = _hash(path)
            if digest in expected:
                found.setdefault(digest, path)
    if set(found) != expected:
        raise FileNotFoundError(f"frozen inputs missing: {sorted(expected - set(found))}")
    return found


def consolidate(
    ledger_path: Path,
    run_root: Path,
    source_roots: list[Path],
    site_map_path: Path,
) -> tuple[dict, dict]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if (
        ledger.get("schema") != "lex_webarena_full_acceptance_ledger/1"
        or ledger.get("counts", {}).get("pass") != 812
        or ledger.get("counts", {}).get("unresolved") != 0
        or [row.get("task_id") for row in ledger.get("rows", [])] != list(range(812))
    ):
        raise ValueError("accepted ledger must contain all 812 passing tasks in official order")
    runs = {
        _hash(progress): progress.parent for progress in run_root.glob("*/progress.jsonl")
    }
    selected = {row["attempts"][-1]["progress_sha256"] for row in ledger["rows"]}
    if not selected <= set(runs):
        raise FileNotFoundError("accepted progress evidence is missing")
    inputs = {
        digest: json.loads((runs[digest] / "inputs.json").read_text(encoding="utf-8"))
        for digest in selected
    }
    expected = {
        digest
        for binding in inputs.values()
        for digest in (
            binding["catalog_sha256"],
            binding["portable_suite_sha256"],
            binding["site_map_sha256"],
        )
    }
    files = _source_files(source_roots, expected)
    catalogs = {
        digest: json.loads(files[digest].read_text(encoding="utf-8"))
        for digest in {binding["catalog_sha256"] for binding in inputs.values()}
    }
    suites = {
        digest: {
            int(task.task_id.removeprefix("webarena-")): task
            for task in load_suite(files[digest]).tasks
        }
        for digest in {binding["portable_suite_sha256"] for binding in inputs.values()}
    }
    raw_suites = {
        digest: {
            int(task["id"].removeprefix("webarena-")): task
            for task in json.loads(files[digest].read_text(encoding="utf-8"))["tasks"]
        }
        for digest in suites
    }
    target_sites = load_remote_sites(site_map_path)
    rows: list[dict] = []
    portable_tasks: list[dict] = []
    for accepted in ledger["rows"]:
        task_id = accepted["task_id"]
        attempt = accepted["attempts"][-1]
        run_digest = attempt["progress_sha256"]
        binding = inputs[run_digest]
        source_catalog = catalogs[binding["catalog_sha256"]]
        if source_catalog["site_map_sha256"] != binding["site_map_sha256"]:
            raise ValueError(f"task {task_id}: source deployment binding differs")
        source_row = source_catalog["rows"][task_id]
        if source_row["task_id"] != task_id or attempt["status"] != "pass":
            raise ValueError(f"task {task_id}: accepted source row differs")
        if source_row["mode"] == "portable":
            suite_hash = binding["portable_suite_sha256"]
            actions = list(suites[suite_hash][task_id].tool_calls)
            portable_tasks.append(raw_suites[suite_hash][task_id])
        else:
            actions = source_row["source_actions"]
        source_sites = _row_source_sites(source_catalog, source_row)
        run_sites = load_remote_sites(files[binding["site_map_sha256"]])
        old_digest = _json_sha256(
            rebind_remote_calls(actions, run_sites, set(TRUSTED_RECORDED_HOSTS), source_sites)
        )
        progress_lines = (runs[run_digest] / "progress.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        progress = next(
            json.loads(line)
            for line in progress_lines
            if json.loads(line)["task_id"] == task_id
        )
        if old_digest != progress["executed_actions_sha256"]:
            raise ValueError(f"task {task_id}: accepted actions differ from source inputs")
        # Older catalogs relied on the run's deployment map and did not list
        # their source origins. Carry those origins into the new binding.
        rebind_sources = {**run_sites, **source_sites}
        row = dict(source_row)
        row["source_site_urls"] = {
            site.value: url for site, url in rebind_sources.items()
        }
        row["bound_calls_sha256"] = _json_sha256(
            rebind_remote_calls(actions, target_sites, set(TRUSTED_RECORDED_HOSTS), rebind_sources)
        )
        row["accepted_progress_sha256"] = run_digest
        rows.append(row)
    suite = {"schema": "lex_browser_use_tasks/4", "tasks": portable_tasks}
    catalog = {
        "schema": SCHEMA,
        "task_ids": list(range(812)),
        "site_map_sha256": _hash(site_map_path),
        "portable_suite_sha256": _json_file_sha256(suite),
        "accepted_ledger_sha256": _hash(ledger_path),
        "rows": rows,
    }
    return catalog, suite


def _json_file_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, action="append", required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--output-catalog", type=Path, required=True)
    parser.add_argument("--output-suite", type=Path, required=True)
    args = parser.parse_args()
    if args.output_catalog.exists() or args.output_suite.exists():
        raise FileExistsError("consolidated catalog or suite already exists")
    catalog, suite = consolidate(
        args.ledger, args.run_root, args.source_root, args.site_map
    )
    args.output_suite.parent.mkdir(parents=True, exist_ok=True)
    args.output_catalog.parent.mkdir(parents=True, exist_ok=True)
    args.output_suite.write_text(
        json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_catalog.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _load_inputs(args.output_catalog, args.output_suite, args.site_map)
    print(json.dumps({"tasks": 812, "catalog_sha256": _hash(args.output_catalog)}))


if __name__ == "__main__":
    main()
