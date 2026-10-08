#!/usr/bin/env python3
"""Package selected one-case isolated runs as one provider replacement cohort."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.record_io import load_jsonl
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from harness.web_fetch.safety import contained_path, validate_report_ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude-case-ids", default="")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists")
    excluded = set(filter(None, args.exclude_case_ids.split(",")))
    receipt = json.loads((args.runs / "isolated-runs.json").read_text())
    provider = receipt["provider"]
    cases, records, batches, children = [], [], [], []
    comparison = None
    for item in receipt["runs"]:
        if item["case_id"] in excluded:
            continue
        child = contained_path(args.runs, item["directory"])
        dataset = json.loads((child / "dataset.json").read_text())
        if len(dataset["cases"]) != 1 or dataset["cases"][0]["id"] != item["case_id"]:
            raise ValueError("Child dataset identity mismatch")
        comparison = comparison or dataset.get("comparison", {})
        cases.extend(dataset["cases"])
        child_rows = load_jsonl(child / "results.jsonl")
        if len(child_rows) != 1 or child_rows[0]["provider"] != provider:
            raise ValueError("Child result identity mismatch")
        child_batches = json.loads((child / "execution.json").read_text())["batches"]
        require_verified_reference_checks(child_batches, cases=dataset,
                                          records=child_rows, source=child)
        records.extend(child_rows); batches.extend(child_batches); children.append(child)
    if not records:
        raise ValueError("No selected isolated runs")
    dataset = {
        "id": "headless-provider-replacement",
        "name": provider + " selected one-call replacements",
        "cases": cases,
        "coverage_plan": {track: [provider] for track in
                          dict.fromkeys(case.get("track", "web") for case in cases)},
        "comparison": comparison or {},
    }
    validate_report_ids(dataset, records)
    args.output.mkdir(parents=True)
    for child in children:
        for folder in ("artifacts", "sources", "list-references", "reference-checks"):
            origin = child / folder
            if not origin.exists():
                continue
            for item in origin.rglob("*"):
                if not item.is_file():
                    continue
                target = contained_path(args.output, item.relative_to(child).as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() != item.read_bytes():
                    raise ValueError("Evidence collision: " + str(item.relative_to(child)))
                if not target.exists():
                    shutil.copy2(item, target)
    (args.output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    (args.output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in records))
    (args.output / "execution.json").write_text(
        json.dumps({"batches": batches}, ensure_ascii=False, indent=2) + "\n")
    print("Packaged", provider, len(records), "one-call replacements")


if __name__ == "__main__":
    main()
