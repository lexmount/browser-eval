"""Replace the position-based assignee click in WebArena task 658."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256
from scripts.run_webarena_full_catalog import TRUSTED_RECORDED_HOSTS, _hash, _load_inputs, _source_sites


TASK_ID = 658
OLD_SELECTOR = ':nth-match(a[href="#"]:visible, 4)'
NEW_SELECTOR = 'a[href="#"]:visible:has-text("@Roshanjossey")'


def repair_catalog(catalog_path: Path, site_map_path: Path) -> dict:
    source = json.loads(catalog_path.read_text(encoding="utf-8"))
    target = dict(source)
    target["predecessor_catalog_sha256"] = _hash(catalog_path)
    target["rows"] = [dict(row) for row in source["rows"]]
    row = target["rows"][TASK_ID]
    if row["task_id"] != TASK_ID or row["mode"] != "direct":
        raise ValueError("task 658 is no longer a direct trajectory")
    actions = [dict(action) for action in row["source_actions"]]
    matches = [action for action in actions if action.get("selector") == OLD_SELECTOR]
    if len(matches) != 1:
        raise ValueError("task 658 assignee action changed")
    matches[0]["selector"] = NEW_SELECTOR
    row["source_actions"] = actions
    row["source_kind"] = "revised_direct_trajectory"
    row["bound_calls_sha256"] = _json_sha256(
        rebind_remote_calls(
            actions,
            load_remote_sites(site_map_path),
            set(TRUSTED_RECORDED_HOSTS),
            _source_sites(target),
        )
    )
    target["repairs"] = list(source.get("repairs") or []) + [{
        "kind": "direct_action_revision",
        "task_ids": [TASK_ID],
        "reason": "select the officially requested Roshanjossey assignee by visible identity",
    }]
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
    result = repair_catalog(args.catalog, args.site_map)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _load_inputs(args.output, args.portable_suite, args.site_map)
    print(json.dumps({"repaired_task_ids": [TASK_ID], "catalog_sha256": _hash(args.output)}))


if __name__ == "__main__":
    main()
