"""Freeze accepted WebArena trajectories into one deployment-bound catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.record_io import load_jsonl
from harness.trajectory_replay.catalog_contracts import (
    ACCEPTANCE_SCHEMAS,
    ACTION_OVERRIDES_SCHEMA,
    CATALOG_SCHEMA,
    TRUSTED_RECORDED_HOSTS,
    apply_action_overrides,
    catalog_action_overrides,
    file_sha256,
    load_action_overrides,
    load_catalog_inputs,
    row_source_sites,
)
from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256
from harness.trajectory_replay.webarena_runtime_source import verify_webarena_runtime_source


def _source_files(roots: list[Path], expected: set[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root in roots:
        for path in root.rglob("*.json"):
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError(f"source file is a symlink: {path}")
            if not any(word in path.name for word in ("catalog", "suite", "sites")):
                continue
            digest = file_sha256(path)
            if digest in expected:
                found.setdefault(digest, path)
    if set(found) != expected:
        raise FileNotFoundError(f"frozen inputs missing: {sorted(expected - set(found))}")
    return found


def _verified_run_directories(
    ledger: dict, run_root: Path, selected_progress: set[str]
) -> dict[str, Path]:
    runs = {
        file_sha256(progress): progress.parent for progress in run_root.glob("*/progress.jsonl")
    }
    ledger_runs = {row["progress_sha256"]: row for row in ledger.get("runs") or []}
    if len(ledger_runs) != len(ledger.get("runs") or []):
        raise ValueError("accepted ledger contains duplicate run bindings")
    if not selected_progress <= set(runs):
        raise FileNotFoundError("accepted progress evidence is missing")
    if not selected_progress <= set(ledger_runs):
        raise ValueError("accepted attempts are not bound to ledger runs")
    for digest in selected_progress:
        inputs_path = runs[digest] / "inputs.json"
        if file_sha256(inputs_path) != ledger_runs[digest]["inputs_sha256"]:
            raise ValueError("run inputs changed after acceptance audit")
    return runs


def _catalog_rows_by_id(catalog: dict) -> dict[int, dict]:
    rows = catalog.get("rows") or []
    indexed = {row.get("task_id"): row for row in rows}
    if len(indexed) != len(rows) or set(indexed) != set(catalog.get("task_ids") or []):
        raise ValueError("source catalog task rows do not match its declared order")
    return indexed


def _load_bound_action_overrides(
    inputs: dict[str, dict], action_overrides_paths: list[Path]
) -> dict[str, tuple[Path, dict[int, list[dict]]]]:
    sources: dict[str, tuple[Path, dict[int, list[dict]]]] = {}
    for path in action_overrides_paths:
        override_map, digest = load_action_overrides(path)
        if digest in sources:
            raise ValueError("duplicate external action overrides source")
        sources[digest] = (path, override_map)
    required = {
        binding["action_overrides_sha256"]
        for binding in inputs.values()
        if "action_overrides_sha256" in binding
    }
    if set(sources) - required:
        raise ValueError("external action overrides are not bound to an accepted run")
    return sources


def _source_action_overrides_path(
    binding: dict,
    source_catalog: dict,
    override_sources: dict[str, tuple[Path, dict[int, list[dict]]]],
) -> Path | None:
    catalog_digest = source_catalog.get("action_overrides_sha256")
    if binding.get("action_overrides_sha256") != catalog_digest:
        raise ValueError("run action override binding differs from its source catalog")
    if catalog_digest is None:
        return None
    source = override_sources.get(catalog_digest)
    return source[0] if source is not None else None


def _target_action_amendments(
    task_id: int,
    source_amendments: list[dict],
    target_override_map: dict[int, list[dict]],
) -> list[dict]:
    target_amendments = target_override_map.get(task_id, [])
    if sorted(source_amendments, key=lambda row: row["action_index"]) != sorted(
        target_amendments, key=lambda row: row["action_index"]
    ):
        raise ValueError(
            f"task {task_id}: target action overrides differ from its accepted execution"
        )
    return target_amendments


def consolidate(
    ledger_path: Path,
    run_root: Path,
    source_roots: list[Path],
    site_map_path: Path,
    action_overrides_paths: list[Path] | None = None,
    target_action_overrides_path: Path | None = None,
) -> tuple[dict, dict]:
    verify_webarena_runtime_source()
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if (
        ledger.get("schema") not in ACCEPTANCE_SCHEMAS
        or ledger.get("counts", {}).get("pass") != ledger.get("counts", {}).get("total")
        or ledger.get("counts", {}).get("unresolved") != 0
        or not ledger.get("rows")
        or len({row.get("task_id") for row in ledger.get("rows", [])})
        != len(ledger.get("rows", []))
    ):
        raise ValueError("accepted ledger must contain every declared passing task in order")
    selected = {row["attempts"][-1]["progress_sha256"] for row in ledger["rows"]}
    runs = _verified_run_directories(ledger, run_root, selected)
    inputs = {
        digest: json.loads((runs[digest] / "inputs.json").read_text(encoding="utf-8"))
        for digest in selected
    }
    override_sources = _load_bound_action_overrides(inputs, action_overrides_paths or [])
    if target_action_overrides_path is not None:
        target_override_map, target_override_sha256 = load_action_overrides(
            target_action_overrides_path
        )
    else:
        target_override_map = {}
        target_override_sha256 = None
    if override_sources and target_override_sha256 is None:
        raise ValueError("consolidation requires an explicit target action overrides source")
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
    catalog_rows = {digest: _catalog_rows_by_id(catalog) for digest, catalog in catalogs.items()}
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
    for binding in inputs.values():
        source_catalog = catalogs[binding["catalog_sha256"]]
        source_override_path = _source_action_overrides_path(
            binding, source_catalog, override_sources
        )
        if source_catalog.get("action_overrides_sha256") is None or source_override_path:
            load_catalog_inputs(
                files[binding["catalog_sha256"]],
                files[binding["portable_suite_sha256"]],
                files[binding["site_map_sha256"]],
                action_overrides_path=source_override_path,
            )
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
        try:
            source_row = catalog_rows[binding["catalog_sha256"]][task_id]
        except KeyError as exc:
            raise ValueError(
                f"task {task_id}: accepted task is absent from source catalog"
            ) from exc
        if source_row["task_id"] != task_id or attempt["status"] != "pass":
            raise ValueError(f"task {task_id}: accepted source row differs")
        if source_row["mode"] == "portable":
            suite_hash = binding["portable_suite_sha256"]
            actions = list(suites[suite_hash][task_id].tool_calls)
            portable_tasks.append(raw_suites[suite_hash][task_id])
        else:
            actions = source_row["source_actions"]
        source_amendments = catalog_action_overrides(source_catalog, source_row)
        target_amendments = _target_action_amendments(
            task_id, source_amendments, target_override_map
        )
        actions = apply_action_overrides(actions, source_amendments, task_id=task_id)
        source_sites = row_source_sites(source_catalog, source_row)
        run_sites = load_remote_sites(files[binding["site_map_sha256"]])
        old_digest = _json_sha256(
            rebind_remote_calls(actions, run_sites, set(TRUSTED_RECORDED_HOSTS), source_sites)
        )
        progress_rows = load_jsonl(runs[run_digest] / "progress.jsonl")
        progress = next(row for row in progress_rows if row["task_id"] == task_id)
        if old_digest != progress["executed_actions_sha256"]:
            raise ValueError(f"task {task_id}: accepted actions differ from source inputs")
        # Older catalogs relied on the run's deployment map and did not list
        # their source origins. Carry those origins into the new binding.
        rebind_sources = {**run_sites, **source_sites}
        row = dict(source_row)
        if target_amendments:
            row["action_overrides"] = {
                "schema": ACTION_OVERRIDES_SCHEMA,
                "source_sha256": target_override_sha256,
                "amendments_sha256": _json_sha256(target_amendments),
                "amendments": target_amendments,
            }
        else:
            row.pop("action_overrides", None)
        row["source_site_urls"] = {site.value: url for site, url in rebind_sources.items()}
        row["bound_calls_sha256"] = _json_sha256(
            rebind_remote_calls(actions, target_sites, set(TRUSTED_RECORDED_HOSTS), rebind_sources)
        )
        row["accepted_progress_sha256"] = run_digest
        rows.append(row)
    suite = {"schema": "lex_browser_use_tasks/4", "tasks": portable_tasks}
    catalog = {
        "schema": CATALOG_SCHEMA,
        "task_ids": [row["task_id"] for row in rows],
        "site_map_sha256": file_sha256(site_map_path),
        "portable_suite_sha256": _json_file_sha256(suite),
        "accepted_ledger_sha256": file_sha256(ledger_path),
        "rows": rows,
    }
    row_override_sources = {
        row["action_overrides"]["source_sha256"]
        for row in rows
        if row.get("action_overrides") is not None
    }
    if len(row_override_sources) > 1:
        raise ValueError("accepted rows mix action override sources")
    if row_override_sources:
        catalog["action_overrides_sha256"] = row_override_sources.pop()
    embedded_overrides = {
        row["task_id"]: row["action_overrides"]["amendments"]
        for row in rows
        if row.get("action_overrides") is not None
    }
    if embedded_overrides != target_override_map:
        raise ValueError("accepted rows differ from the target action overrides source")
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
    parser.add_argument("--action-overrides", type=Path, action="append", default=[])
    parser.add_argument("--target-action-overrides", type=Path)
    parser.add_argument("--output-catalog", type=Path, required=True)
    parser.add_argument("--output-suite", type=Path, required=True)
    args = parser.parse_args()
    if args.output_catalog.exists() or args.output_suite.exists():
        raise FileExistsError("consolidated catalog or suite already exists")
    catalog, suite = consolidate(
        args.ledger,
        args.run_root,
        args.source_root,
        args.site_map,
        action_overrides_paths=args.action_overrides,
        target_action_overrides_path=args.target_action_overrides,
    )
    args.output_suite.parent.mkdir(parents=True, exist_ok=True)
    args.output_catalog.parent.mkdir(parents=True, exist_ok=True)
    args.output_suite.write_text(
        json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_catalog.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    load_catalog_inputs(
        args.output_catalog,
        args.output_suite,
        args.site_map,
        accepted_ledger_path=args.ledger,
        action_overrides_path=args.target_action_overrides,
    )
    print(
        json.dumps(
            {"tasks": len(catalog["rows"]), "catalog_sha256": file_sha256(args.output_catalog)}
        )
    )


if __name__ == "__main__":
    main()
