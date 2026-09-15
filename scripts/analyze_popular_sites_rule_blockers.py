#!/usr/bin/env python3
"""Analyze fresh provider-free captures for cases blocked by the rule audit."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.stable_tasks import evaluate_stable_task, validate_acceptance_contract
from scripts.audit_popular_sites_rules_v2 import projected_answer, projected_links
from scripts.validate_popular_sites_rule_audit import anchors


VERIFICATION = re.compile(
    r"(?:human verification|confirm you are human|security verification|"
    r"additional verification required|安全验证|验证中心|身份核实|访问频繁|"
    r"欢迎登录|登录页面)",
    re.I,
)
VERIFICATION_PATH = re.compile(r"/(?:account/unhuman|new/login\.aspx|v2/app/general_page)", re.I)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_correction(case: dict, correction: dict | None) -> dict:
    case = deepcopy(case)
    if not correction:
        return case
    contract = case["acceptance_contract"]
    if "stable_text_groups" in correction:
        contract["stable_text_anchors"] = anchors(
            correction["stable_text_groups"], named_link=contract["schema"] == "named_link"
        )
    if "entry_path" in correction:
        contract["entry_path"] = correction["entry_path"]
    validate_acceptance_contract(contract)
    return case


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/popular-sites-dataset.json")
    parser.add_argument("--audit", type=Path, default=ROOT / "research/popular-sites-rules-audit-v2.json")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name in ("dataset", "audit", "capture", "output"):
        setattr(args, name, getattr(args, name).resolve())
    if args.output.exists():
        raise SystemExit("Refusing to overwrite append-only blocker analysis")

    dataset = json.loads(args.dataset.read_text())
    audit = json.loads(args.audit.read_text())
    capture = json.loads(args.capture.read_text())
    cases = {case["id"]: case for case in dataset["cases"]}
    corrections = {row["case_id"]: row for row in audit["corrections"]}
    blockers = {row["case_id"] for row in audit["blockers"]}
    resolved_followup = {row["case_id"] for row in audit.get("resolved_followup", [])}
    expected_inventory = blockers | resolved_followup
    captured = {row["case_id"]: row for row in capture["cases"]}
    if set(captured) != expected_inventory:
        raise ValueError("Fresh capture must contain exactly the blocked and resolved-follow-up inventory")

    findings = []
    for case_id in sorted(expected_inventory):
        case = apply_correction(cases[case_id], corrections.get(case_id))
        expected_host = urlsplit(case["url"]).hostname
        rounds = []
        for round_ in captured[case_id]["rounds"]:
            content = projected_answer(round_)
            result = evaluate_stable_task(
                content, case["acceptance_contract"], case["url"], eligible=True,
                links=projected_links(round_),
            )
            final_url = round_.get("final_url", "")
            verification = bool(
                (round_.get("http_status") or 0) >= 400
                or VERIFICATION.search("\n".join((round_.get("title", ""), content)))
                or VERIFICATION_PATH.search(urlsplit(final_url).path)
            )
            rounds.append({
                "round": round_["round"],
                "http_status": round_.get("http_status"),
                "final_url": final_url,
                "final_host": urlsplit(final_url).hostname,
                "expected_host": expected_host,
                "verification_or_login": verification,
                "candidate_pass": result["stable_task_pass"],
                "errors": result["stable_task_errors"],
                "unique_records": result["stable_unique_record_count"],
                "anchor_hits": result["stable_anchor_hits"],
                "anchor_total": result["stable_anchor_total"],
            })
        normal = [row for row in rounds if not row["verification_or_login"]]
        conclusion = (
            "resolved_current_url" if len(normal) == 3 and all(row["candidate_pass"] for row in normal)
            else "normal_page_but_contract_unstable" if normal
            else "current_url_not_independently_reproducible"
        )
        findings.append({
            "case_id": case_id,
            "schema": case["acceptance_contract"]["schema"],
            "candidate_correction_applied": case_id in corrections,
            "conclusion": conclusion,
            "rounds": rounds,
        })

    summary = {
        "cases": len(findings),
        "resolved_current_url": sum(row["conclusion"] == "resolved_current_url" for row in findings),
        "normal_page_but_contract_unstable": sum(row["conclusion"] == "normal_page_but_contract_unstable" for row in findings),
        "current_url_not_independently_reproducible": sum(
            row["conclusion"] == "current_url_not_independently_reproducible" for row in findings
        ),
    }
    output = {
        "schema_version": 1,
        "purpose": "Provider-blind follow-up analysis of the original eleven rule-audit blockers",
        "bindings": {
            "dataset_sha256": digest(args.dataset),
            "audit_sha256": digest(args.audit),
            "capture_sha256": digest(args.capture),
        },
        "provider_outputs_read": False,
        "provider_calls": 0,
        "summary": summary,
        "cases": findings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
