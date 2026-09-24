"""Bind a frozen full WebArena action catalog to another site deployment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.full_catalog import (
    TRUSTED_RECORDED_HOSTS,
    file_sha256,
    load_catalog_inputs,
    row_source_sites,
)
from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256


def rebind_catalog(
    catalog_path: Path,
    suite_path: Path,
    site_map_path: Path,
    *,
    revised_task_ids: set[int] | None = None,
    source_site_map_path: Path | None = None,
) -> dict:
    source = json.loads(catalog_path.read_text(encoding="utf-8"))
    suite = load_suite(suite_path)
    tasks = {int(task.task_id.removeprefix("webarena-")): task for task in suite.tasks}
    sites = load_remote_sites(site_map_path)
    target = dict(source)
    target["predecessor_catalog_sha256"] = file_sha256(catalog_path)
    target["site_map_sha256"] = file_sha256(site_map_path)
    target["portable_suite_sha256"] = file_sha256(suite_path)
    if source_site_map_path is not None:
        if file_sha256(source_site_map_path) != source["site_map_sha256"]:
            raise ValueError("source site map differs from the predecessor catalog")
        target["source_site_urls"] = {
            site.value: url for site, url in load_remote_sites(source_site_map_path).items()
        }
    target["rows"] = []
    revised = set(revised_task_ids or ())
    if revised and target["site_map_sha256"] != source["site_map_sha256"]:
        raise ValueError("action revisions must keep the site map fixed")
    for original in source["rows"]:
        row = dict(original)
        task_id = row["task_id"]
        actions = (
            list(tasks[task_id].tool_calls) if row["mode"] == "portable" else row["source_actions"]
        )
        row["bound_calls_sha256"] = _json_sha256(
            rebind_remote_calls(
                actions, sites, set(TRUSTED_RECORDED_HOSTS), row_source_sites(target, row)
            )
        )
        if task_id in revised:
            if (
                row["mode"] != "portable"
                or row["bound_calls_sha256"] == original["bound_calls_sha256"]
            ):
                raise ValueError(f"task {task_id} has no revised portable actions")
            row["source_kind"] = "revised_portable_suite"
            row["source_suite_sha256"] = file_sha256(suite_path)
            row.pop("source_path", None)
        target["rows"].append(row)
    if revised - {row["task_id"] for row in target["rows"]}:
        raise ValueError("revised task IDs are outside the catalog")
    if revised:
        target["repairs"] = list(source.get("repairs") or []) + [
            {
                "kind": "portable_action_revision",
                "task_ids": sorted(revised),
                "suite_sha256": file_sha256(suite_path),
            }
        ]
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--source-site-map", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revised-task-id", type=int, action="append", default=[])
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = rebind_catalog(
        args.catalog,
        args.portable_suite,
        args.site_map,
        revised_task_ids=set(args.revised_task_id),
        source_site_map_path=args.source_site_map,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    load_catalog_inputs(args.output, args.portable_suite, args.site_map)
    print(json.dumps({"tasks": len(result["rows"]), "catalog_sha256": file_sha256(args.output)}))


if __name__ == "__main__":
    main()
