"""Audit catalog evaluate actions that require explicit function-body semantics."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from harness.trajectory_replay.catalog_contracts import file_sha256, load_action_overrides
from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.webarena_replay import _json_sha256
from harness.web_fetch.safety import unlinked_path, write_new_file

AUDIT_SCHEMA = "lex_webarena_replay_action_override_audit/1"

_NODE_AUDIT = r"""
const vm = require("vm");
const rows = JSON.parse(require("fs").readFileSync(0, "utf8"));
const findings = [];
const invalid = [];
for (const row of rows) {
  try {
    new vm.Script(row.script);
    continue;
  } catch (scriptError) {
    try {
      new vm.Script(`(async () => {\n${row.script}\n})`);
      if (row.script_mode === "function_body") continue;
      findings.push({
        task_id: row.task_id,
        mode: row.mode,
        action_index: row.action_index,
        precondition_action_sha256: row.precondition_action_sha256,
      });
    } catch (bodyError) {
      invalid.push({
        task_id: row.task_id,
        action_index: row.action_index,
        script_error: String(scriptError.message),
        function_body_error: String(bodyError.message),
      });
    }
  }
}
process.stdout.write(JSON.stringify({findings, invalid}));
"""


def _evaluate_actions(catalog: dict, suite_path: Path) -> list[dict]:
    catalog_rows = catalog.get("rows") or []
    if any(row.get("mode") == "portable" for row in catalog_rows):
        suite = load_suite(suite_path)
        portable = {
            int(task.task_id.removeprefix("webarena-")): list(task.tool_calls)
            for task in suite.tasks
        }
    else:
        portable = {}
    rows = []
    for catalog_row in catalog_rows:
        task_id = catalog_row["task_id"]
        mode = catalog_row.get("mode")
        if mode == "portable":
            actions = portable[task_id]
        elif mode == "direct":
            actions = catalog_row.get("source_actions") or []
        else:
            raise ValueError(f"task {task_id}: unknown catalog mode")
        for action_index, action in enumerate(actions, 1):
            if action.get("action") != "evaluate":
                continue
            script = action.get("script")
            if not isinstance(script, str) or not script.strip():
                raise ValueError(f"task {task_id}: evaluate action has no script")
            rows.append(
                {
                    "task_id": task_id,
                    "mode": mode,
                    "action_index": action_index,
                    "precondition_action_sha256": _json_sha256(action),
                    "script_mode": action.get("script_mode"),
                    "script": script,
                }
            )
    return rows


def scan_function_body_actions(
    catalog_path: Path, suite_path: Path, *, node_binary: str = "node"
) -> dict:
    """Parse every source evaluate action under Script and async function-body grammars."""

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    evaluate_actions = _evaluate_actions(catalog, suite_path)
    process = subprocess.run(
        [node_binary, "-e", _NODE_AUDIT],
        input=json.dumps(evaluate_actions, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(f"JavaScript action audit failed: {process.stderr.strip()}")
    result = json.loads(process.stdout)
    if result.get("invalid"):
        raise ValueError(f"evaluate actions are invalid under both grammars: {result['invalid']}")
    return {
        "schema": AUDIT_SCHEMA,
        "catalog_sha256": file_sha256(catalog_path),
        "portable_suite_sha256": file_sha256(suite_path),
        "evaluate_action_count": len(evaluate_actions),
        "function_body_findings": result["findings"],
    }


def validate_function_body_overrides(audit: dict, action_overrides_path: Path) -> None:
    """Require one exact script_mode amendment for every parser finding and no extras."""

    if audit.get("schema") != AUDIT_SCHEMA:
        raise ValueError("action override audit schema is invalid")
    override_map, _digest = load_action_overrides(action_overrides_path)
    finding_modes = _finding_modes(audit)
    actual = [
        {
            "task_id": amendment["task_id"],
            "mode": finding_modes.get(amendment["task_id"], {}).get(amendment["action_index"]),
            "action_index": amendment["action_index"],
            "precondition_action_sha256": amendment["precondition_action_sha256"],
        }
        for amendments in override_map.values()
        for amendment in amendments
        if amendment["set"].get("script_mode") == "function_body"
    ]
    if actual != audit.get("function_body_findings"):
        raise ValueError("function-body action overrides differ from the complete static audit")


def validate_catalog_action_audit(
    audit_path: Path,
    catalog_path: Path,
    suite_path: Path,
    action_overrides_path: Path,
) -> dict:
    """Re-run the parser audit at release and bind it to the exact frozen inputs."""

    if audit_path.is_symlink() or not audit_path.is_file():
        raise ValueError("action override audit must be a regular file")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if (
        audit.get("schema") != AUDIT_SCHEMA
        or audit.get("catalog_sha256") != file_sha256(catalog_path)
        or audit.get("portable_suite_sha256") != file_sha256(suite_path)
    ):
        raise ValueError("action override audit changed its catalog or suite binding")
    live = scan_function_body_actions(catalog_path, suite_path)
    if live != audit:
        raise ValueError("action override audit differs from the live catalog scan")
    validate_function_body_overrides(audit, action_overrides_path)
    return audit


def _finding_modes(audit: dict) -> dict[int, dict[int, str]]:
    modes: dict[int, dict[int, str]] = {}
    for finding in audit.get("function_body_findings") or []:
        modes.setdefault(finding["task_id"], {})[finding["action_index"]] = finding["mode"]
    return modes


def main_audit(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--action-overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = unlinked_path(Path(args.output).expanduser())
    if output.exists():
        raise ValueError("output already exists")
    audit = scan_function_body_actions(args.catalog, args.portable_suite)
    validate_function_body_overrides(audit, args.action_overrides)
    write_new_file(output, (json.dumps(audit, indent=2) + "\n").encode())
    print(
        json.dumps(
            {
                "evaluate_actions": audit["evaluate_action_count"],
                "function_body_findings": len(audit["function_body_findings"]),
            }
        )
    )
