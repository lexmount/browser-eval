"""Mark currently inaccessible full-text references without removing task checks.

The independent capture remains evidence of the access failure. Stable facts,
patterns and other task rules are preserved; only snapshot-derived full-text
metrics and their distributed body regions become unavailable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def apply(dataset_path: Path, captures: Path, case_ids: set[str]) -> list[dict]:
    data = json.loads(dataset_path.read_text())
    audit = {row["case_id"]: row for row in json.loads((captures / "audit.json").read_text())}
    cases = {case["id"]: case for case in data["cases"]}
    if case_ids - cases.keys() or case_ids - audit.keys():
        raise ValueError("Unknown case or missing independent capture")
    receipts = []
    for case_id in sorted(case_ids):
        case, observed = cases[case_id], audit[case_id]
        if observed.get("status") == 200 and not observed.get("missing_facts"):
            raise ValueError(f"{case_id}: capture is usable; refusing to discard its reference")
        previous = case.get("ground_truth_main_content", "")
        case["ground_truth_main_content"] = ""
        case.pop("body_requirements", None)
        case["source_verification"] = {
            "state": "unavailable",
            "checked_at": observed["checked_at"],
            "http_status": observed.get("status"),
            "method": "Independent HTTP GET; current complete text reference unavailable",
            "selector": None,
            "reference_sha256": None,
            "note": (
                "Current full-text reference unavailable. Stable task checks remain active; "
                "full-text precision, recall and F1 are unmeasured."
            ),
        }
        receipts.append({
            "case_id": case_id,
            "checked_at": observed["checked_at"],
            "status": observed.get("status"),
            "final_url": observed.get("final_url"),
            "response_sha256": observed.get("sha256"),
            "previous_reference_sha256": (
                __import__("hashlib").sha256(previous.encode()).hexdigest() if previous else None
            ),
            "missing_facts": observed.get("missing_facts", []),
            "decision": "retain_task_checks_and_remove_unavailable_snapshot_text_metrics",
        })
    dataset_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    for receipt in receipts:
        target = dataset_path.parent / "sources" / receipt["case_id"] / "unavailable-reference.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--case-ids", required=True)
    args = parser.parse_args()
    rows = apply(args.dataset, args.captures, set(args.case_ids.split(",")))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
