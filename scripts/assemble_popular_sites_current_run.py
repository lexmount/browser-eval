#!/usr/bin/env python3
# ruff: noqa: E402, E501 -- repo imports follow path setup; evidence notes stay whole
"""Assemble 52 immutable prior calls and 48 new calls into one 100-site run."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import summarize
from harness.web_fetch.report import succeeded
from scripts.rescore_content import rescore


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text().splitlines()]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def projected_batches(path: Path, role: str) -> list[dict]:
    payload = json.loads((path / "execution.json").read_text())
    result = []
    for batch in payload["batches"]:
        item = deepcopy(batch)
        item["projection"] = {
            "kind": "popular-sites-current-authority",
            "role": role,
            "source_execution": str(path / "execution.json"),
            "source_execution_sha256": sha256(path / "execution.json"),
        }
        result.append(item)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inherited", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "evaluation/popular-sites-dataset.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.inherited = args.inherited.resolve()
    args.new = args.new.resolve()
    args.dataset = args.dataset.resolve()
    args.output = args.output.resolve()
    if args.output.exists():
        parser.error("Output already exists; refusing to overwrite evidence")

    dataset = json.loads(args.dataset.read_text())
    cases = dataset["cases"]
    expected_ids = [case["id"] for case in cases]
    inherited_ids = expected_ids[:52]
    new_ids = expected_ids[52:]
    inherited_dataset = json.loads((args.inherited / "dataset.json").read_text())
    if inherited_dataset["cases"] != cases[:52]:
        raise ValueError("The inherited 52 cases differ from the frozen combined cases")

    source_rows = {
        "inherited": {row["case_id"]: row for row in read_records(args.inherited)},
        "new": {row["case_id"]: row for row in read_records(args.new)},
    }
    if set(source_rows["inherited"]) != set(inherited_ids):
        raise ValueError("Inherited evidence is not the exact 52-case authority")
    if set(source_rows["new"]) != set(new_ids):
        raise ValueError("New evidence is not the exact 48-case frozen queue")

    args.output.mkdir(parents=True)
    (args.output / "dataset.json").write_bytes(args.dataset.read_bytes())
    selected = []
    for index, case in enumerate(cases):
        role = "inherited" if index < 52 else "new"
        source_root = args.inherited if role == "inherited" else args.new
        row = deepcopy(source_rows[role][case["id"]])
        if row["url"] != case["url"]:
            raise ValueError(f"{case['id']}: result URL differs from the frozen case URL")
        selected.append(row)
        source_artifact = source_root / row["artifact_path"]
        target_artifact = args.output / row["artifact_path"]
        target_artifact.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_artifact, target_artifact)

    for child in (ROOT / "evaluation/sources").iterdir():
        if child.is_dir() and (child.name in expected_ids or child.name == "popular-sites"):
            shutil.copytree(child, args.output / "sources" / child.name)
    (args.output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in selected)
    )
    write_json(
        args.output / "execution.json",
        {
            "batches": projected_batches(args.inherited, "inherited_unchanged_formal_calls")
            + projected_batches(args.new, "new_frozen_cases"),
            "assembly": {
                "inherited_case_count": 52,
                "new_provider_call_count": 48,
                "total_case_count": 100,
                "selection_policy": "One authoritative formal call per method-task pair; no best-of selection.",
            },
        },
    )

    before = sha256(args.output / "results.jsonl")
    changes = rescore(args.output, allow_rule_revision=True)
    after = sha256(args.output / "results.jsonl")
    write_json(
        args.output / "scoring-revisions.json",
        [
            {
                "kind": "combined_popular_sites_offline_rescore",
                "scored_at": datetime.now(UTC).isoformat(),
                "dataset_sha256": sha256(args.output / "dataset.json"),
                "input_results_sha256": before,
                "output_results_sha256": after,
                "task_decision_changes": changes,
                "provider_calls": 0,
                "inherited_formal_calls": 52,
                "new_formal_calls": 48,
                "provider_output_used_as_rule_source": False,
                "immutable_call_fields": "output bytes/hash, request status, timestamps and elapsed time retained",
            }
        ],
    )
    rescored = read_records(args.output)
    case_map = {case["id"]: case for case in cases}
    write_json(args.output / "summary.json", summarize(rescored))
    write_json(
        args.output / "task-rerun.json",
        {
            "schema_version": 1,
            "kind": "popular_sites_incremental_assembly",
            "created_at": datetime.now(UTC).isoformat(),
            "case_count": 100,
            "inherited_unchanged_cases": inherited_ids,
            "new_provider_call_case_ids": new_ids,
            "provider_calls_added": 48,
            "provider_calls_reused": 52,
            "selection_policy": "No unchanged case was rerun; no failed result was retried or replaced.",
            "provider_output_used_as_rule_source": False,
        },
    )
    print(
        json.dumps(
            {
                "cases": len(rescored),
                "passes": sum(succeeded(row, case_map[row["case_id"]]) for row in rescored),
                "inherited_calls": 52,
                "new_calls": 48,
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
