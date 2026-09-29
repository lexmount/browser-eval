"""Bind a frozen WebArena action catalog to another site deployment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.catalog_contracts import (
    ACTION_OVERRIDES_SCHEMA,
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


def rebind_catalog(
    catalog_path: Path,
    suite_path: Path,
    site_map_path: Path,
    *,
    revised_task_ids: set[int] | None = None,
    source_site_map_path: Path | None = None,
    action_overrides_path: Path | None = None,
    revise_action_overrides: bool = False,
) -> dict:
    if revise_action_overrides and action_overrides_path is None:
        raise ValueError("action override revision requires an overrides source")
    source = json.loads(catalog_path.read_text(encoding="utf-8"))
    suite = load_suite(suite_path)
    tasks = {int(task.task_id.removeprefix("webarena-")): task for task in suite.tasks}
    sites = load_remote_sites(site_map_path)
    target = dict(source)
    target["predecessor_catalog_sha256"] = file_sha256(catalog_path)
    target["site_map_sha256"] = file_sha256(site_map_path)
    target["portable_suite_sha256"] = file_sha256(suite_path)
    if action_overrides_path is not None:
        override_map, override_sha256 = load_action_overrides(action_overrides_path)
        existing_sha256 = source.get("action_overrides_sha256")
        has_existing_overrides = "action_overrides_sha256" in source or any(
            row.get("action_overrides") is not None for row in source.get("rows") or []
        )
        if (
            has_existing_overrides
            and existing_sha256 != override_sha256
            and not revise_action_overrides
        ):
            raise ValueError("action overrides differ from the predecessor catalog")
        if has_existing_overrides and existing_sha256 == override_sha256:
            embedded = {
                row["task_id"]: amendments
                for row in source.get("rows") or []
                if (amendments := catalog_action_overrides(source, row))
            }
            if embedded != override_map:
                raise ValueError("action overrides differ from the predecessor catalog rows")
        target["action_overrides_sha256"] = override_sha256
    else:
        if "action_overrides_sha256" in source or any(
            row.get("action_overrides") is not None for row in source.get("rows") or []
        ):
            raise ValueError("catalog rebind requires its external action overrides source")
        override_map = {}
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
        amendments = override_map.get(task_id, [])
        actions = apply_action_overrides(actions, amendments, task_id=task_id)
        if amendments:
            row["action_overrides"] = {
                "schema": ACTION_OVERRIDES_SCHEMA,
                "source_sha256": target["action_overrides_sha256"],
                "amendments_sha256": _json_sha256(amendments),
                "amendments": amendments,
            }
        else:
            row.pop("action_overrides", None)
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
    if set(override_map) - {row["task_id"] for row in target["rows"]}:
        raise ValueError("action overrides contain tasks outside the catalog")
    if revised:
        target["repairs"] = list(source.get("repairs") or []) + [
            {
                "kind": "portable_action_revision",
                "task_ids": sorted(revised),
                "suite_sha256": file_sha256(suite_path),
            }
        ]
    if revise_action_overrides:
        if not has_existing_overrides or existing_sha256 == target["action_overrides_sha256"]:
            raise ValueError("action override revision must replace an existing source")
        target["repairs"] = list(target.get("repairs") or []) + [
            {
                "kind": "action_override_revision",
                "predecessor_action_overrides_sha256": existing_sha256,
                "action_overrides_sha256": target["action_overrides_sha256"],
                "task_ids": sorted(override_map),
            }
        ]
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--source-site-map", type=Path)
    parser.add_argument("--action-overrides", type=Path)
    parser.add_argument("--revise-action-overrides", action="store_true")
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
        action_overrides_path=args.action_overrides,
        revise_action_overrides=args.revise_action_overrides,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    load_catalog_inputs(
        args.output,
        args.portable_suite,
        args.site_map,
        action_overrides_path=args.action_overrides,
    )
    print(json.dumps({"tasks": len(result["rows"]), "catalog_sha256": file_sha256(args.output)}))


if __name__ == "__main__":
    main()
