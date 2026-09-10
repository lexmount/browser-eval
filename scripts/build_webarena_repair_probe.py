"""Build a one-task, non-canonical suite for Chromium repair validation."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.browser_use.suite import load_suite


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_probe(suite: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    if proposal.get("schema") != "lex_browser_use_repair_proposal/1":
        raise ValueError("repair proposal schema is invalid")
    task_id = str(proposal.get("task_id") or "")
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError("repair proposal must identify exactly one suite task")
    append_actions = proposal.get("append_actions") or []
    replace_actions = proposal.get("replace_actions") or []
    response_builder = proposal.get("response_builder")
    reason = str(proposal.get("reason") or "").strip()
    harness_revalidation = proposal.get("harness_revalidation") is True
    if harness_revalidation and (
        append_actions or replace_actions or response_builder is not None
    ):
        raise ValueError("harness revalidation cannot modify actions or response builder")
    if not harness_revalidation and (
        bool(append_actions) == bool(replace_actions)
        or not isinstance(response_builder, dict)
    ):
        raise ValueError(
            "repair proposal requires exactly one of append_actions or replace_actions, "
            "plus response builder and reason"
        )
    if not reason:
        raise ValueError("repair proposal requires a reason")

    task = deepcopy(matches[0])
    original = deepcopy(task["trajectory"])
    if not harness_revalidation:
        if replace_actions:
            task["trajectory"]["actions"] = deepcopy(replace_actions)
        else:
            task["trajectory"]["actions"].extend(deepcopy(append_actions))
    provenance = dict(task["trajectory"].get("provenance") or {})
    provenance.update(
        {
            "kind": (
                "harness_revalidation_probe"
                if harness_revalidation
                else "codex_repair_probe"
            ),
            "original_trajectory_sha256": _sha256_json(original),
            "proposal_sha256": _sha256_json(proposal),
        }
    )
    task["trajectory"]["provenance"] = provenance
    task["ground_truth"]["expected_actions"] = [
        action["action"] for action in task["trajectory"]["actions"]
    ]
    if not harness_revalidation:
        task["ground_truth"]["response_builder"] = deepcopy(response_builder)
    task["ground_truth"]["qualification"] = {
        "status": "pending",
        "reason": reason,
    }
    result = deepcopy(suite)
    result["tasks"] = [task]
    benchmark = dict(result.get("benchmark") or {})
    benchmark["repair_probe"] = {
        "task_id": task_id,
        "proposal_sha256": _sha256_json(proposal),
        "non_canonical": True,
        "harness_revalidation": harness_revalidation,
    }
    result["benchmark"] = benchmark
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_probe(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.proposal.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_suite(args.output)


if __name__ == "__main__":
    main()
