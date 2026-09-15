#!/usr/bin/env python3
"""Validate proposed 100-site rule corrections without measured provider output."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.stable_tasks import evaluate_stable_task, validate_acceptance_contract
from harness.web_fetch.dynamic_lists import extract_named_entries
from scripts.audit_popular_sites_rules_v2 import projected_answer, projected_links


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def anchors(groups: list[list[str]], *, named_link: bool) -> list[dict]:
    return [
        {
            "id": f"stable-anchor-{index}",
            "alternatives": group,
            **({"match_scope": "non_record_lines"} if named_link else {}),
        }
        for index, group in enumerate(groups, 1)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/popular-sites-dataset.json")
    parser.add_argument("--audit", type=Path, default=ROOT / "research/popular-sites-rules-audit-v2.json")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, required=True)
    parser.add_argument("--followup", type=Path, required=True)
    parser.add_argument("--ordinary-browser", type=Path, required=True)
    parser.add_argument("--ocr", type=Path, required=True)
    parser.add_argument("--mechanical", type=Path, required=True)
    args = parser.parse_args()
    for name in ("dataset", "audit", "capture", "supplement", "followup", "ordinary_browser", "ocr", "mechanical"):
        setattr(args, name, getattr(args, name).resolve())

    dataset = json.loads(args.dataset.read_text())
    audit = json.loads(args.audit.read_text())
    capture = json.loads(args.capture.read_text())
    supplement = json.loads(args.supplement.read_text())
    followup = json.loads(args.followup.read_text())
    ordinary_browser = json.loads(args.ordinary_browser.read_text())
    bindings = audit["bindings"]
    expected_hashes = {
        "dataset_sha256": args.dataset,
        "primary_capture_sha256": args.capture,
        "ten_second_supplement_sha256": args.supplement,
        "blocker_followup_capture_sha256": args.followup,
        "ordinary_browser_recheck_sha256": args.ordinary_browser,
        "ocr_diagnostic_sha256": args.ocr,
        "mechanical_audit_sha256": args.mechanical,
    }
    for key, path in expected_hashes.items():
        if bindings[key] != digest(path):
            raise ValueError(f"Audit binding changed: {key}")
    if audit["policy"]["provider_outputs_used"] is not False:
        raise ValueError("Audit must remain provider-blind")

    cases = {case["id"]: deepcopy(case) for case in dataset["cases"]}
    blockers = {item["case_id"] for item in audit["blockers"]}
    corrections = {item["case_id"]: item for item in audit["corrections"]}
    if (
        len(cases) != audit["reviewed_case_count"]
        or len(blockers) != audit["summary"]["blocked_cases"]
        or len(corrections) != audit["summary"]["proposed_rule_corrections"]
    ):
        raise ValueError("Audit scope/counts changed")
    for case_id, correction in corrections.items():
        contract = cases[case_id]["acceptance_contract"]
        if "stable_text_groups" in correction:
            contract["stable_text_anchors"] = anchors(
                correction["stable_text_groups"], named_link=contract["schema"] == "named_link"
            )
        if "entry_path" in correction:
            contract["entry_path"] = correction["entry_path"]
        validate_acceptance_contract(contract)
        if contract["schema"] == "page_identity" and len(contract["stable_text_anchors"]) < 3:
            raise ValueError(f"Undeclared page_identity exception: {case_id}")

    captured = {item["case_id"]: item["rounds"] for item in capture["cases"]}
    supplemental = {item["case_id"]: item["rounds"] for item in supplement["cases"]}
    followup_rounds = {item["case_id"]: item["rounds"] for item in followup["cases"]}
    positive_failures = []
    passing_observations = []
    for case_id, correction in corrections.items():
        if case_id in blockers:
            continue
        source_rounds = (
            followup_rounds[case_id]
            if correction.get("validation_source") == "blocker_followup_capture"
            else captured[case_id] + supplemental.get(case_id, [])
        )
        rounds = [
            row for row in source_rounds
            if row["state"] == "captured" and (row.get("http_status") or 0) < 400
        ]
        for row in rounds:
            result = evaluate_stable_task(
                projected_answer(row), cases[case_id]["acceptance_contract"], cases[case_id]["url"],
                eligible=True, links=projected_links(row),
            )
            if not result["stable_task_pass"]:
                positive_failures.append({
                    "case_id": case_id,
                    "round": row["round"],
                    "errors": result["stable_task_errors"],
                })
            else:
                passing_observations.append((case_id, row))

    ordinary_failures = []
    ordinary_cases = {row["case_id"]: row for row in ordinary_browser["cases"]}
    for case_id, count_field in (("challenge-01", "named_shop_links"), ("challenge-09", "named_title_links")):
        row = ordinary_cases[case_id]
        if len(row.get("rounds", [])) != 3:
            ordinary_failures.append({"case_id": case_id, "reason": "not_three_rounds"})
            continue
        for round_ in row["rounds"]:
            booleans = [value for key, value in round_.items() if key != "round" and isinstance(value, bool)]
            if not booleans or not all(booleans) or round_.get(count_field, 0) < 3:
                ordinary_failures.append({"case_id": case_id, "round": round_["round"]})

    collisions = []
    for source_id, rounds in captured.items():
        source = next((row for row in rounds if row["state"] == "captured" and (row.get("http_status") or 0) < 400), None)
        if not source:
            continue
        for target_id, target in cases.items():
            if source_id == target_id or target_id in blockers:
                continue
            result = evaluate_stable_task(
                projected_answer(source), target["acceptance_contract"], target["url"],
                eligible=True, links=projected_links(source),
            )
            if result["stable_task_pass"]:
                collisions.append({"source_case": source_id, "passed_rule": target_id})

    mutation_failures = []
    anchor_mutations_checked = 0
    short_record_mutations_checked = 0
    blocked_page_mutations_checked = 0
    for case_id, row in passing_observations:
        case = cases[case_id]
        contract = case["acceptance_contract"]
        content = projected_answer(row)
        links = projected_links(row)
        for anchor in contract.get("stable_text_anchors", []):
            mutated = content
            for alternative in anchor["alternatives"]:
                mutated = re.sub(re.escape(alternative), "", mutated, flags=re.I)
            result = evaluate_stable_task(
                mutated, contract, case["url"], eligible=True, links=links,
            )
            anchor_result = next(item for item in result["stable_anchor_results"] if item["id"] == anchor["id"])
            if anchor_result["pass"]:
                continue  # Exact deletion did not remove a normalized/variant occurrence.
            anchor_mutations_checked += 1
            if result["stable_task_pass"]:
                mutation_failures.append({
                    "case_id": case_id,
                    "round": row["round"],
                    "mutation": f"removed_required_anchor:{anchor['id']}",
                })
        blocked = evaluate_stable_task(
            "Verify that you are human\n" + content,
            contract,
            case["url"],
            eligible=True,
            links=links,
        )
        blocked_page_mutations_checked += 1
        if blocked["stable_task_pass"] or "blocked_page" not in blocked["stable_task_errors"]:
            mutation_failures.append({
                "case_id": case_id,
                "round": row["round"],
                "mutation": "inserted_blocked_page",
            })
        if contract["schema"] == "named_link":
            entries = extract_named_entries("", contract, case["url"], links)
            minimum = contract["minimum_records"]
            if len(entries) >= minimum:
                shortened = entries[:minimum - 1]
                result = evaluate_stable_task(
                    content, contract, case["url"], eligible=True, links=shortened,
                )
                short_record_mutations_checked += 1
                if result["stable_task_pass"] or "too_few_complete_records" not in result["stable_task_errors"]:
                    mutation_failures.append({
                        "case_id": case_id,
                        "round": row["round"],
                        "mutation": "reduced_named_records_below_minimum",
                    })

    summary = {
        "reviewed_cases": len(cases),
        "corrections": len(corrections),
        "blockers": len(blockers),
        "positive_control_failures": positive_failures + ordinary_failures,
        "false_negative_control_failures": positive_failures + ordinary_failures,
        "cross_case_false_positive_collisions": collisions,
        "false_positive_mutation_failures": mutation_failures,
        "mutation_controls_checked": {
            "required_anchor_removed": anchor_mutations_checked,
            "named_records_reduced_below_minimum": short_record_mutations_checked,
            "blocked_page_inserted": blocked_page_mutations_checked,
        },
        "provider_outputs_read": False,
        "provider_calls": 0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if positive_failures or ordinary_failures or collisions or mutation_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
