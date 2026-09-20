"""Inventory comparison symmetry and declared benchmark edits without rescoring.

Equal inputs do not establish unbiased task selection or valid acceptance rules.
This inventory is a starting point for source and counterexample review, not a
fairness certificate. Measured output belongs outside the repository.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


def audit_dataset_changes(before: list[dict], after: list[dict], selected: set[int]) -> list[dict]:
    def index(tasks: list[dict]) -> dict[int, dict]:
        rows = {row["task_id"]: row for row in tasks}
        if len(rows) != len(tasks):
            raise ValueError("duplicate dataset task identity")
        return rows

    old, new = index(before), index(after)
    changes = []
    for task in sorted(old.keys() | new.keys()):
        if old.get(task) == new.get(task):
            continue
        left, right = old.get(task, {}), new.get(task, {})
        fields = {
            key: {"before": left.get(key), "after": right.get(key)}
            for key in sorted(left.keys() | right.keys())
            if left.get(key) != right.get(key)
        }
        changes.append({"task_id": task, "selected": task in selected,
                        "change": "added" if not left else "removed" if not right else "modified",
                        "fields": fields})
    return changes


def audit_inputs(results: dict, profile: dict, corrections: dict) -> dict[str, Any]:
    participants = [method["id"] for method in profile["methods"]]
    if not participants or len(set(participants)) != len(participants):
        raise ValueError("participant registry must be unique and nonempty")
    task_count = profile["task_selection"]["task_count"]
    rows: dict[int, dict[str, dict]] = {}
    for row in results["attempts"]:
        task = row["task_id"]
        method = row["participant_id"]
        if method not in participants or method in rows.setdefault(task, {}):
            raise ValueError("unknown participant or duplicate logical result")
        rows[task][method] = row
    if len(rows) != task_count or any(set(row) != set(participants) for row in rows.values()):
        raise ValueError("incomplete declared task-by-method matrix")
    unequal_actions = []
    unequal_contracts = []
    for task, methods in sorted(rows.items()):
        baseline = methods[participants[0]]
        if any(row["planned_actions"] != baseline["planned_actions"] for row in methods.values()):
            unequal_actions.append(task)
        if any(row["task_contract_sha256"] != baseline["task_contract_sha256"] for row in methods.values()):
            unequal_contracts.append(task)
    edits = []
    for record in corrections["corrections"]:
        if record["task_id"] not in rows:
            continue
        operations = record["operations"]
        edits.append({
            "task_id": record["task_id"],
            "correction_id": record["correction_id"],
            "changes_intent": any(op["path"] == "/intent" for op in operations),
            "removed_evaluators": [op["path"] for op in operations if op["op"] == "remove" and op["path"].startswith("/eval/")],
            "operations": operations,
            "replay_assertions": record.get("replay_assertions", []),
            "network_event_alternatives": record.get("network_event_alternatives", []),
            "has_bound_root_cause_and_regressions": bool(record.get("root_cause") and len(record.get("regression_ids", [])) >= 2),
        })
    return {
        "schema": "lex_replay_input_audit/1",
        "scope": "Input symmetry and declared local edits only; not causal attribution or fairness approval",
        "participants": participants,
        "task_count": task_count,
        "logical_result_count": len(results["attempts"]),
        "unequal_actions_task_ids": unequal_actions,
        "unequal_contract_task_ids": unequal_contracts,
        "qualification_reference_engine": profile.get("qualification", {}).get("reference_engine"),
        "seed_inventory_base_kind": profile.get("seed_inventory", {}).get("base_kind"),
        "local_contract_edits": edits,
        "requires_separate_review": ["upstream fork changes", "task selection and qualification bias", "acceptance weakening and counterexamples", "source mechanisms and failure attribution"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=Path("evaluation/trajectory-replay/webarena-verified/current-run.json"))
    parser.add_argument("--corrections", type=Path, default=Path("evaluation/trajectory-replay/webarena-verified/task-corrections.json"))
    parser.add_argument("--fork-repository", type=Path)
    parser.add_argument("--upstream-ref")
    parser.add_argument("--fork-ref")
    args = parser.parse_args()
    def read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    results = read(args.results)
    audit = audit_inputs(results, read(args.profile), read(args.corrections))
    fork_args = (args.fork_repository, args.upstream_ref, args.fork_ref)
    if any(fork_args):
        if not all(fork_args):
            parser.error("fork inventory requires repository and both explicit refs")
        datasets = []
        commits = []
        for ref in (args.upstream_ref, args.fork_ref):
            commit = subprocess.check_output(["git", "-C", str(args.fork_repository), "rev-parse", "--verify", f"{ref}^{{commit}}"], text=True).strip()
            commits.append(commit)
            datasets.append(json.loads(subprocess.check_output(["git", "-C", str(args.fork_repository), "show", f"{commit}:assets/dataset/webarena-verified.json"], text=True)))
        selected = {row["task_id"] for row in results["attempts"]}
        audit["fork"] = {"upstream_commit": commits[0], "fork_commit": commits[1],
                         "dataset_changes": audit_dataset_changes(*datasets, selected)}
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
