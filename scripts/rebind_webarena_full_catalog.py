"""Bind a frozen full WebArena action catalog to another site deployment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256
from scripts.run_webarena_full_catalog import TRUSTED_RECORDED_HOSTS, _hash, _load_inputs


def rebind_catalog(catalog_path: Path, suite_path: Path, site_map_path: Path) -> dict:
    source = json.loads(catalog_path.read_text(encoding="utf-8"))
    suite = load_suite(suite_path)
    tasks = {int(task.task_id.removeprefix("webarena-")): task for task in suite.tasks}
    sites = load_remote_sites(site_map_path)
    target = dict(source)
    target["predecessor_catalog_sha256"] = _hash(catalog_path)
    target["site_map_sha256"] = _hash(site_map_path)
    target["rows"] = []
    for original in source["rows"]:
        row = dict(original)
        task_id = row["task_id"]
        actions = (
            list(tasks[task_id].tool_calls)
            if row["mode"] == "portable"
            else row["source_actions"]
        )
        row["bound_calls_sha256"] = _json_sha256(
            rebind_remote_calls(actions, sites, set(TRUSTED_RECORDED_HOSTS))
        )
        target["rows"].append(row)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = rebind_catalog(args.catalog, args.portable_suite, args.site_map)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _load_inputs(args.output, args.portable_suite, args.site_map)
    print(json.dumps({"tasks": len(result["rows"]), "catalog_sha256": _hash(args.output)}))


if __name__ == "__main__":
    main()
