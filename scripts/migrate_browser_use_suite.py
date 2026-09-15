"""Migrate a legacy curated browser-use suite to the common v4 contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from harness.trajectory_replay.suite import LEGACY_SCHEMA, SCHEMA, TRAJECTORY_SCHEMA, load_suite


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _semantic_contract(payload: dict[str, Any]) -> list[dict[str, Any]]:
    legacy = payload.get("schema") == LEGACY_SCHEMA
    result: list[dict[str, Any]] = []
    for task in payload.get("tasks") or []:
        actions = (
            task.get("tool_calls") if legacy else (task.get("trajectory") or {}).get("actions")
        )
        evaluator = (
            {"kind": "exact_outputs", "assertions": task.get("assertions") or []}
            if legacy
            else task.get("evaluator")
        )
        environment = {"kind": "public_web"} if legacy else task.get("environment")
        result.append(
            {
                "id": task.get("id"),
                "title": task.get("title"),
                "difficulty": task.get("difficulty"),
                "tags": task.get("tags") or [],
                "actions": actions,
                "assertions": task.get("assertions") or [],
                "ground_truth": task.get("ground_truth"),
                "source": task.get("source"),
                "evaluator": evaluator,
                "environment": environment,
            }
        )
    return result


def _contract_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        _semantic_contract(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return _sha256_bytes(encoded)


def migrate(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if payload.get("schema") != LEGACY_SCHEMA:
        raise ValueError(f"input suite must use {LEGACY_SCHEMA}")
    before_contract = _contract_sha256(payload)
    migrated = {key: value for key, value in payload.items() if key != "tasks"}
    migrated["schema"] = SCHEMA
    tasks: list[dict[str, Any]] = []
    for raw in payload.get("tasks") or []:
        task = {key: value for key, value in raw.items() if key != "tool_calls"}
        task["trajectory"] = {
            "schema": TRAJECTORY_SCHEMA,
            "provenance": {
                "kind": "repository_curated",
                "migrated_from_schema": LEGACY_SCHEMA,
            },
            "actions": raw["tool_calls"],
        }
        task["evaluator"] = {
            "kind": "exact_outputs",
            "assertions": raw.get("assertions") or [],
        }
        task["environment"] = {"kind": "public_web"}
        tasks.append(task)
    migrated["tasks"] = tasks
    after_contract = _contract_sha256(migrated)
    if after_contract != before_contract:
        raise RuntimeError("v4 migration changed the executable semantic contract")
    return migrated, before_contract


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--ground-truth-evidence", type=Path, required=True)
    args = parser.parse_args()

    original_bytes = args.suite.read_bytes()
    original = json.loads(original_bytes)
    migrated, contract_sha256 = migrate(original)
    migrated_bytes = (json.dumps(migrated, indent=2, ensure_ascii=False) + "\n").encode()

    evidence = json.loads(args.ground_truth_evidence.read_text(encoding="utf-8"))
    old_sha256 = _sha256_bytes(original_bytes)
    if evidence.get("dataset_sha256") != old_sha256:
        raise ValueError("ground-truth evidence is not bound to the legacy suite")
    evidence["dataset_sha256"] = _sha256_bytes(migrated_bytes)
    evidence["schema_migration"] = {
        "from": LEGACY_SCHEMA,
        "to": SCHEMA,
        "legacy_dataset_sha256": old_sha256,
        "semantic_contract_sha256": contract_sha256,
        "executable_contract_changed": False,
    }

    args.suite.write_bytes(migrated_bytes)
    args.ground_truth_evidence.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    load_suite(args.suite)


if __name__ == "__main__":
    main()
