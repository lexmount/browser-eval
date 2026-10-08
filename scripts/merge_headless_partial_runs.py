#!/usr/bin/env python3
"""Merge retained completed attempts with one-case isolated completions."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.safety import contained_path, validate_report_ids


def rows(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "results.jsonl").read_text().splitlines()]


def equivalent_task(left: dict, right: dict) -> bool:
    left, right = deepcopy(left), deepcopy(right)
    left.pop("source_verification", None)
    right.pop("source_verification", None)
    return left == right


def copy_tree_files(source: Path, output: Path, relative: str) -> None:
    origin = source / relative
    if not origin.exists():
        return
    for item in origin.rglob("*"):
        if not item.is_file():
            continue
        target = contained_path(output, item.relative_to(source).as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != item.read_bytes():
                raise ValueError("Evidence collision: " + str(item.relative_to(source)))
        else:
            shutil.copy2(item, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--isolated", type=Path, action="append", default=[])
    parser.add_argument("--run", type=Path, action="append", default=[],
                        help="Additional direct run root")
    parser.add_argument("--dataset", type=Path,
                        help="Explicit declared matrix dataset")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists")
    dataset_bytes = read_dataset_bytes(args.dataset or (args.base / "dataset.json"))
    dataset = json.loads(dataset_bytes)
    selected = rows(args.base)
    execution = args.base / "execution.json"
    batches = (json.loads(execution.read_text())["batches"] if execution.exists()
               else [json.loads((args.base / "manifest.json").read_text())])
    if len(batches) != 1:
        raise ValueError("Base must be one retained physical batch")
    base_batch = deepcopy(batches[0])
    source_unexecuted = base_batch.get("unexecuted", [])
    base_batch["unexecuted"] = []
    base_batch.setdefault("projection", {}).update({
        "kind": "completed-attempts-from-retained-incomplete-matrix",
        "source_unexecuted": source_unexecuted,
        "selection": "all retained completed attempts; no score-based selection",
    })
    batches = [base_batch]
    sources = [args.base]
    for child in args.run:
        child_rows = rows(child)
        child_dataset = json.loads((child / "dataset.json").read_text())
        declared = {case["id"]: case for case in dataset["cases"]}
        if any(case["id"] not in declared or not equivalent_task(declared[case["id"]], case)
               for case in child_dataset.get("cases", [])):
            raise ValueError("Direct run task differs: " + str(child))
        selected.extend(child_rows)
        child_batches = json.loads((child / "execution.json").read_text())["batches"]
        require_verified_reference_checks(child_batches, cases=dataset,
                                          records=child_rows, source=child,
                                          equivalent_capture_bindings=True)
        batches.extend(child_batches)
        sources.append(child)
    for root in args.isolated:
        receipt = json.loads((root / "isolated-runs.json").read_text())
        for item in receipt["runs"]:
            child = contained_path(root, item["directory"])
            child_dataset = json.loads((child / "dataset.json").read_text())
            child_cases = child_dataset.get("cases", [])
            expected_case = next(case for case in dataset["cases"]
                                 if case["id"] == item["case_id"])
            if len(child_cases) != 1 or not equivalent_task(child_cases[0], expected_case):
                raise ValueError("Isolated task differs: " + item["case_id"])
            child_rows = rows(child)
            if len(child_rows) != 1:
                raise ValueError("Isolated child does not contain one result")
            selected.extend(child_rows)
            child_batches = json.loads((child / "execution.json").read_text())["batches"]
            require_verified_reference_checks(child_batches, cases=dataset,
                                              records=child_rows, source=child,
                                              equivalent_capture_bindings=True)
            batches.extend(child_batches)
            sources.append(child)
    keys = [(row["case_id"], row["provider"]) for row in selected]
    expected = {(case["id"], provider) for case in dataset["cases"]
                for provider in dataset["coverage_plan"][case.get("track", "web")]}
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("Merged attempts do not exactly cover the declared matrix")
    selected.sort(key=lambda row: (
        next(i for i, case in enumerate(dataset["cases"]) if case["id"] == row["case_id"]),
        dataset["coverage_plan"][next(case.get("track", "web") for case in dataset["cases"]
                                      if case["id"] == row["case_id"])].index(row["provider"])))
    validate_report_ids(dataset, selected)
    require_verified_reference_checks(batches, cases=dataset, records=selected,
                                      source=None, equivalent_capture_bindings=True)
    args.output.mkdir(parents=True)
    for source in sources:
        for relative in ("artifacts", "sources", "list-references", "reference-checks"):
            copy_tree_files(source, args.output, relative)
    (args.output / "dataset.json").write_bytes(dataset_bytes)
    (args.output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in selected))
    (args.output / "execution.json").write_text(
        json.dumps({"batches": batches}, ensure_ascii=False, indent=2) + "\n")
    (args.output / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "attempt_count": len(selected),
        "attempt_ids": [row["attempt_id"] for row in selected],
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "projection": {"kind": "complete-matrix-from-retained-and-isolated-runs",
                       "physical_batch_count": len(batches)},
    }, ensure_ascii=False, indent=2) + "\n")
    print("Merged complete matrix:", len(selected), "attempts in", len(batches), "batches")


if __name__ == "__main__":
    main()
