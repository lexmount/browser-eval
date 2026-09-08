#!/usr/bin/env python3
"""Install reviewed frozen references without changing task rules."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_FIELDS = {"ground_truth_main_content", "body_requirements", "source_verification"}


def task_rules(case: dict) -> dict:
    value = deepcopy(case)
    for field in REFERENCE_FIELDS:
        value.pop(field, None)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--case-ids", required=True)
    args = parser.parse_args()
    wanted = set(args.case_ids.split(","))
    canonical_path = ROOT / "evaluation/dataset.json"
    canonical = json.loads(canonical_path.read_text())
    incoming = json.loads((args.frozen / "dataset.json").read_text())
    current = {case["id"]: case for case in canonical["cases"]}
    new = {case["id"]: case for case in incoming["cases"] if case["id"] in wanted}
    if set(new) != wanted or wanted - set(current):
        raise ValueError("Reference install inventory differs")
    for case_id in sorted(wanted):
        if task_rules(current[case_id]) != task_rules(new[case_id]):
            raise ValueError("Frozen input changed task rules: " + case_id)
        for field in REFERENCE_FIELDS:
            current[case_id][field] = deepcopy(new[case_id].get(field))
        source = args.frozen / "sources" / case_id
        target = ROOT / "evaluation/sources" / case_id
        if not source.is_dir() or not target.is_dir():
            raise ValueError("Source evidence directory missing: " + case_id)
        for item in source.iterdir():
            if item.is_file():
                shutil.copy2(item, target / item.name)
    canonical_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n")
    print("Installed frozen references:", len(wanted))


if __name__ == "__main__":
    main()
