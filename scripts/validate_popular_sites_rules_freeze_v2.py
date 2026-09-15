#!/usr/bin/env python3
"""Validate the frozen 100-site v2 rules without reading measured provider outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.dynamic_lists import extract_named_entries
from harness.web_fetch.stable_tasks import evaluate_stable_task, validate_acceptance_contract
from scripts.audit_popular_sites_rules_v2 import projected_answer, projected_links


APPROVED_IDS = {"challenge-02", "challenge-04", "challenge-06"}
ORDINARY_IDS = {"challenge-01", "challenge-09"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observation_from_round(row: dict) -> dict:
    return {
        "label": str(row.get("round", "capture")),
        "content": projected_answer(row),
        "links": projected_links(row),
    }


def approved_observations(reference: dict) -> dict[str, list[dict]]:
    result = {}
    for case in reference["cases"]:
        content = "\n".join(group[0] for group in case["stable_text_groups"])
        links = [{"title": name, "url": url} for name, url in case["records"]]
        result[case["case_id"]] = [
            {"label": f"approved-{index}", "content": content, "links": links}
            for index, _ in enumerate(case["completed_observation_record_counts"], 1)
        ]
    return result


def ordinary_observations(case: dict) -> list[dict]:
    annotation = case["source_benchmark"]["stable_annotation"]
    source = ROOT / "evaluation" / annotation["anchor_evidence"]
    content = source.read_text(encoding="utf-8")
    reference = json.loads((ROOT / "evaluation/list-references" / f"{case['id']}.json").read_text())
    links = [{"title": row["title"], "url": row["url"]} for row in reference["items"]]
    return [
        {"label": f"ordinary-{index}", "content": content, "links": links}
        for index in range(1, 4)
    ]


def evaluate(case: dict, observation: dict) -> dict:
    return evaluate_stable_task(
        observation["content"],
        case["acceptance_contract"],
        case["url"],
        eligible=True,
        links=observation["links"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/popular-sites-dataset.json")
    parser.add_argument("--rules", type=Path, default=ROOT / "research/popular-sites-rules.json")
    parser.add_argument("--audit", type=Path, default=ROOT / "research/popular-sites-rules-audit-v2.json")
    parser.add_argument("--approval", type=Path, default=ROOT / "research/popular-sites-rules-v2-approval.json")
    parser.add_argument("--approved-reference", type=Path, default=ROOT / "research/popular-sites-approved-contract-reference-2026-09-15.json")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, required=True)
    parser.add_argument("--followup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    if args.output.exists():
        raise SystemExit("Refusing to overwrite final validation evidence")

    dataset = json.loads(args.dataset.read_text())
    rules = json.loads(args.rules.read_text())
    audit = json.loads(args.audit.read_text())
    approval = json.loads(args.approval.read_text())
    approved_reference = json.loads(args.approved_reference.read_text())
    capture = json.loads(args.capture.read_text())
    supplement = json.loads(args.supplement.read_text())
    followup = json.loads(args.followup.read_text())
    cases = {case["id"]: case for case in dataset["cases"]}
    rule_cases = {case["id"]: case for case in rules["cases"]}
    if len(cases) != len(rule_cases) or len(cases) != 100 or set(cases) != set(rule_cases):
        raise ValueError("Frozen dataset and rule registry do not contain the same 100 cases")
    for case_id, case in cases.items():
        rule = rule_cases[case_id]
        if case["url"] != rule["url"] or case["acceptance_contract"] != rule["acceptance_contract"]:
            raise ValueError(f"Dataset/rule mismatch: {case_id}")
        contract = case["acceptance_contract"]
        validate_acceptance_contract(contract)
        if contract["schema"] == "page_identity" and len(contract["stable_text_anchors"]) < 3:
            raise ValueError(f"Undeclared page_identity exception: {case_id}")
        annotation = case.get("source_benchmark", {}).get("stable_annotation", {})
        evidence = annotation.get("anchor_evidence")
        if bool(evidence) != bool(contract["stable_text_anchors"]):
            raise ValueError(f"Stable evidence presence mismatch: {case_id}")
        if evidence:
            path = ROOT / "evaluation" / evidence
            if not path.is_file() or digest(path) != annotation["anchor_evidence_sha256"]:
                raise ValueError(f"Stable evidence hash mismatch: {case_id}")

    if audit["blockers"] or audit["decision_required"]:
        raise ValueError("The audit still has unresolved decisions")
    if approval["status"] != "approved_for_rule_freeze":
        raise ValueError("Final approval is not frozen")
    if approved_reference["provider_outputs_read"] or approved_reference["provider_calls"]:
        raise ValueError("Approved reference is not provider-blind")

    captured = {row["case_id"]: row["rounds"] for row in capture["cases"]}
    supplemented = {row["case_id"]: row["rounds"] for row in supplement["cases"]}
    followed = {row["case_id"]: row["rounds"] for row in followup["cases"]}
    approved = approved_observations(approved_reference)
    use_followup = {
        "global-homepage-016-amazon-com",
        "global-homepage-037-mail-ru",
        "global-homepage-068-indeed-com",
        "global-homepage-072-rakuten-co-jp",
        "global-homepage-081-namu-wiki",
        "china-homepage-058-huaban-com",
    }
    observations: dict[str, list[dict]] = {}
    source_kind = {}
    for case_id, case in cases.items():
        if case_id in APPROVED_IDS:
            rows = approved[case_id]
            source_kind[case_id] = "approved_anonymous_browser"
        elif case_id in ORDINARY_IDS:
            rows = ordinary_observations(case)
            source_kind[case_id] = "ordinary_browser_plus_independent_list_reference"
        else:
            raw = (
                followed[case_id]
                if case_id in use_followup
                else captured[case_id] + supplemented.get(case_id, [])
            )
            rows = [
                observation_from_round(row)
                for row in raw
                if row["state"] == "captured" and (row.get("http_status") or 0) < 400
            ]
            source_kind[case_id] = "followup_capture" if case_id in use_followup else "primary_capture"
        passing = [row for row in rows if evaluate(case, row)["stable_task_pass"]]
        if len(passing) < 3:
            failures = [evaluate(case, row)["stable_task_errors"] for row in rows]
            raise ValueError(f"Fewer than three positive observations for {case_id}: {failures}")
        observations[case_id] = passing[:3]

    collisions = []
    cross_checks = 0
    for source_id, rows in observations.items():
        source = rows[0]
        for target_id, target in cases.items():
            if source_id == target_id:
                continue
            cross_checks += 1
            result = evaluate(target, source)
            if result["stable_task_pass"]:
                collisions.append({"source_case": source_id, "passed_rule": target_id})

    mutation_failures = []
    anchor_checks = 0
    record_checks = 0
    blocked_checks = 0
    wrong_host_checks = 0
    for case_id, rows in observations.items():
        case = cases[case_id]
        contract = case["acceptance_contract"]
        for row in rows:
            for anchor in contract["stable_text_anchors"]:
                mutated = row["content"]
                for alternative in anchor["alternatives"]:
                    mutated = re.sub(re.escape(alternative), "", mutated, flags=re.I)
                result = evaluate(case, {**row, "content": mutated})
                anchor_result = next(item for item in result["stable_anchor_results"] if item["id"] == anchor["id"])
                if anchor_result["pass"]:
                    continue
                anchor_checks += 1
                if result["stable_task_pass"]:
                    mutation_failures.append([case_id, row["label"], f"removed_anchor:{anchor['id']}"])
            blocked_checks += 1
            blocked = evaluate(case, {**row, "content": "Verify that you are human\n" + row["content"]})
            if blocked["stable_task_pass"] or "blocked_page" not in blocked["stable_task_errors"]:
                mutation_failures.append([case_id, row["label"], "inserted_blocked_page"])
            if contract["schema"] == "named_link":
                entries = extract_named_entries("", contract, case["url"], row["links"])
                if len(entries) >= contract["minimum_records"]:
                    record_checks += 1
                    shortened = entries[: contract["minimum_records"] - 1]
                    result = evaluate(case, {**row, "links": shortened})
                    if result["stable_task_pass"] or "too_few_complete_records" not in result["stable_task_errors"]:
                        mutation_failures.append([case_id, row["label"], "records_below_minimum"])
                    wrong_host_checks += 1
                    wrong = [
                        {"title": item["title"], "url": f"https://wrong.invalid/item/{index}"}
                        for index, item in enumerate(entries[: contract["minimum_records"]], 1)
                    ]
                    result = evaluate(case, {**row, "links": wrong})
                    if result["stable_task_pass"]:
                        mutation_failures.append([case_id, row["label"], "wrong_host_records"])

    output = {
        "schema_version": 1,
        "status": "passed" if not collisions and not mutation_failures else "failed",
        "validated_at": "2026-09-15",
        "bindings": {
            "dataset_sha256": digest(args.dataset),
            "rules_sha256": digest(args.rules),
            "audit_sha256": digest(args.audit),
            "approval_sha256": digest(args.approval),
            "approved_reference_sha256": digest(args.approved_reference),
            "primary_capture_sha256": digest(args.capture),
            "supplement_capture_sha256": digest(args.supplement),
            "followup_capture_sha256": digest(args.followup),
        },
        "coverage": {
            "cases": len(cases),
            "positive_observations": sum(len(rows) for rows in observations.values()),
            "positive_observations_per_case": 3,
            "page_identity_exceptions_under_three_groups": 0,
            "cross_case_matrix": "100x99",
            "cross_case_checks": cross_checks,
            "cross_case_false_positive_collisions": collisions,
            "required_anchor_removal_checks": anchor_checks,
            "named_records_below_minimum_checks": record_checks,
            "wrong_host_named_record_checks": wrong_host_checks,
            "blocked_page_insertion_checks": blocked_checks,
            "mutation_failures": mutation_failures,
        },
        "source_kind_by_case": source_kind,
        "provider_outputs_read": False,
        "provider_calls": 0,
        "deferred_methods_run": False,
        "publication_hold_lifted": False,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["coverage"], ensure_ascii=False, indent=2))
    if output["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
