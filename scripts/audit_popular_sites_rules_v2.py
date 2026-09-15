#!/usr/bin/env python3
"""Mechanically audit 100-site rules against provider-free repeated captures."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import fact_in_text, normalize_for_match
from harness.web_fetch.stable_tasks import evaluate_stable_task


VOLATILE = re.compile(
    r"(?:\b20\d{2}\b|\b\d+(?:[.,]\d+)?\s*(?:%|off|views?|comments?|items?)\b|"
    r"[$€£¥]\s*\d|today|tonight|breaking|trending now)", re.I
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def folded(value: str) -> str:
    return normalize_for_match(value or "")


def first(*values: str) -> str:
    return next((value for value in values if value), "")


def surface_values(round_: dict) -> dict[str, list[str]]:
    controls = round_.get("controls", [])
    links = round_.get("links", [])
    images = round_.get("images", [])
    return {
        "visible_dom": [round_.get("visible_text", "")],
        "element_text": [item.get("text", "") for item in round_.get("elements", [])],
        "control_visible": [item.get("visible_text", "") for item in controls],
        "control_accessible": [
            first(item.get("aria_label", ""), item.get("placeholder", ""), item.get("title", ""))
            for item in controls
        ],
        "link_visible": [item.get("text", "") for item in links],
        "link_accessible": [
            first(item.get("aria_label", ""), item.get("title", ""))
            for item in links
        ],
        "image_accessible": [
            first(item.get("alt", ""), item.get("aria_label", ""), item.get("title", ""))
            for item in images
        ],
        "document_title": [round_.get("title", "")],
    }


def match_anchor(anchor: dict, round_: dict) -> dict:
    surfaces = surface_values(round_)
    matches = {}
    for name, values in surfaces.items():
        found = []
        for alternative in anchor["alternatives"]:
            expected = folded(alternative)
            if expected and any(fact_in_text(folded(value), expected) for value in values if value):
                found.append(alternative)
        if found:
            matches[name] = found
    # Pixel text and image alt/title are diagnostics, not stable body evidence.
    # Requiring them would favor fetchers that preserve image metadata and
    # penalize otherwise correct text extraction.
    eligible = set(matches) - {"document_title", "image_accessible"}
    return {
        "round": round_["round"],
        "state": round_["state"],
        "matched": bool(eligible),
        "eligible_surfaces": sorted(eligible),
        "all_surfaces": matches,
        "title_only": bool(matches) and not eligible,
    }


def projected_answer(round_: dict) -> str:
    values = [round_.get("visible_text", "")]
    for item in round_.get("controls", []):
        values.append(first(
            item.get("visible_text", ""), item.get("aria_label", ""),
            item.get("placeholder", ""), item.get("title", ""),
        ))
    for item in round_.get("links", []):
        values.append(first(
            item.get("text", ""), item.get("aria_label", ""),
            item.get("title", ""),
        ))
    return "\n".join(value for value in values if value)


def projected_links(round_: dict) -> list[dict]:
    return [
        {
            "url": item.get("url", ""),
            "title": first(
                item.get("text", ""), item.get("aria_label", ""),
                item.get("title", ""),
            ),
        }
        for item in round_.get("links", [])
    ]


def candidate_strings(rounds: list[dict]) -> list[dict]:
    per_round = []
    displays: dict[str, str] = {}
    sources: dict[str, set[str]] = {}
    for round_ in rounds:
        current = set()
        for source, values in surface_values(round_).items():
            if source in {"visible_dom", "document_title", "image_accessible"}:
                continue
            for value in values:
                value = re.sub(r"\s+", " ", value).strip()
                key = folded(value)
                if (2 <= len(value) <= 120 and key and not VOLATILE.search(value)
                        and not re.fullmatch(r"[\W\d_]+", value)):
                    current.add(key)
                    displays.setdefault(key, value)
                    sources.setdefault(key, set()).add(source)
        per_round.append(current)
    if not per_round:
        return []
    common = set.intersection(*per_round)
    return [
        {"text": displays[key], "sources": sorted(sources[key])}
        for key in sorted(common, key=lambda value: (len(value), value))
    ][:100]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/popular-sites-dataset.json")
    parser.add_argument("--rules", type=Path, default=ROOT / "research/popular-sites-rules.json")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, action="append", default=[])
    parser.add_argument("--ocr", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name in ("dataset", "rules", "capture", "output"):
        setattr(args, name, getattr(args, name).resolve())
    args.supplement = [path.resolve() for path in args.supplement]
    args.ocr = args.ocr.resolve() if args.ocr else None
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an audit record")
    dataset = json.loads(args.dataset.read_text())
    rules = json.loads(args.rules.read_text())
    capture = json.loads(args.capture.read_text())
    if len(dataset.get("cases", [])) != 100 or len(rules.get("cases", [])) != 100:
        raise SystemExit("Expected matching 100-case inputs")
    if capture.get("contract", {}).get("provider_calls") is not False:
        raise SystemExit("Audit capture is not provider-free")
    cases = {case["id"]: case for case in dataset["cases"]}
    captures = {item["case_id"]: item for item in capture["cases"]}
    if set(cases) != set(captures):
        raise SystemExit("Capture inventory differs from the frozen dataset")
    supplements: dict[str, list[dict]] = {}
    for path in args.supplement:
        payload = json.loads(path.read_text())
        if payload.get("contract", {}).get("provider_calls") is not False:
            raise SystemExit(f"Supplement is not provider-free: {path}")
        for item in payload.get("cases", []):
            supplements.setdefault(item["case_id"], []).extend(item.get("rounds", []))
    ocr_text: dict[str, str] = {}
    if args.ocr:
        ocr = json.loads(args.ocr.read_text())
        if ocr.get("ocr_is_scoring_evidence") is not False:
            raise SystemExit("OCR diagnostic does not prohibit scoring use")
        ocr_text = {
            item["case_id"]: "\n".join(item.get("lines", []))
            for item in ocr.get("cases", [])
        }

    first_round_text = {
        case_id: folded(projected_answer(next(
            round_ for round_ in item["rounds"] if round_["state"] == "captured"
        )))
        for case_id, item in captures.items()
    }
    findings = []
    for case_id, case in cases.items():
        contract = case["acceptance_contract"]
        rounds = [round_ for round_ in captures[case_id]["rounds"] if round_["state"] == "captured"]
        diagnostic_rounds = [
            round_ for round_ in supplements.get(case_id, []) if round_["state"] == "captured"
        ]
        scored_rounds = [
            (round_, evaluate_stable_task(
                projected_answer(round_), contract, case["url"],
                eligible=True, links=projected_links(round_),
            ))
            for round_ in rounds
        ]
        scored_diagnostics = [
            (round_, evaluate_stable_task(
                projected_answer(round_), contract, case["url"],
                eligible=True, links=projected_links(round_),
            ))
            for round_ in diagnostic_rounds
        ]
        anchor_rows = []
        for anchor in contract.get("stable_text_anchors", []):
            matches = []
            for round_, scored in scored_rounds:
                observation = match_anchor(anchor, round_)
                observation["surface_matched"] = observation["matched"]
                observation["matched"] = next(
                    item["pass"] for item in scored["stable_anchor_results"]
                    if item["id"] == anchor["id"]
                )
                matches.append(observation)
            diagnostic_matches = []
            for round_, scored in scored_diagnostics:
                observation = match_anchor(anchor, round_)
                observation["surface_matched"] = observation["matched"]
                observation["matched"] = next(
                    item["pass"] for item in scored["stable_anchor_results"]
                    if item["id"] == anchor["id"]
                )
                diagnostic_matches.append(observation)
            other_cases = []
            for other_id, text in first_round_text.items():
                if other_id == case_id:
                    continue
                if any(fact_in_text(text, folded(value)) for value in anchor["alternatives"]):
                    other_cases.append(other_id)
            anchor_rows.append({
                "id": anchor["id"],
                "alternatives": anchor["alternatives"],
                "present_rounds": sum(item["matched"] for item in matches),
                "captured_rounds": len(rounds),
                "currently_repeatable": len(rounds) == 3 and all(item["matched"] for item in matches),
                "title_only_rounds": sum(item["title_only"] for item in matches),
                "observations": matches,
                "diagnostic_observations": diagnostic_matches,
                "other_case_matches": other_cases,
                "individually_discriminative": not other_cases,
                "ocr_viewport_match": any(
                    fact_in_text(folded(ocr_text.get(case_id, "")), folded(value))
                    for value in anchor["alternatives"]
                ),
                "possible_pixel_only": not any(item["matched"] for item in matches) and any(
                    fact_in_text(folded(ocr_text.get(case_id, "")), folded(value))
                    for value in anchor["alternatives"]
                ),
            })
        positive = []
        for round_, result in scored_rounds:
            positive.append({
                "round": round_["round"],
                "pass": result["stable_task_pass"],
                "errors": result["stable_task_errors"],
                "unique_records": result["stable_unique_record_count"],
            })
        repeatable = all(row["currently_repeatable"] for row in anchor_rows)
        positive_all = len(positive) == 3 and all(item["pass"] for item in positive)
        status = (
            "capture_incomplete" if len(rounds) != 3 else
            "candidate_keep" if repeatable and positive_all else
            "candidate_revision_or_access_blocker"
        )
        findings.append({
            "case_id": case_id,
            "url": case["url"],
            "schema": contract["schema"],
            "anchor_group_count": len(anchor_rows),
            "page_identity_under_three": contract["schema"] == "page_identity" and len(anchor_rows) < 3,
            "capture_rounds": len(rounds),
            "final_urls": sorted({round_.get("final_url") for round_ in rounds}),
            "http_statuses": [round_.get("http_status") for round_ in rounds],
            "diagnostic_final_urls": sorted({round_.get("final_url") for round_ in diagnostic_rounds}),
            "diagnostic_http_statuses": [round_.get("http_status") for round_ in diagnostic_rounds],
            "anchors": anchor_rows,
            "positive_controls": positive,
            "repeated_candidate_text": candidate_strings(rounds),
            "mechanical_status": status,
            "human_decision": "pending",
        })

    # Cross-case collisions are diagnostics: generic words may be valid members
    # of a jointly discriminative contract, so they never auto-reject a rule.
    collisions = []
    for source_id, item in captures.items():
        round_ = next((row for row in item["rounds"] if row["state"] == "captured"), None)
        if not round_:
            continue
        content, links = projected_answer(round_), projected_links(round_)
        for target_id, target in cases.items():
            if target_id == source_id:
                continue
            result = evaluate_stable_task(
                content, target["acceptance_contract"], target["url"],
                eligible=True, links=links,
            )
            if result["stable_task_pass"]:
                collisions.append({"source_case": source_id, "passed_rule": target_id})
    output = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "purpose": "Provider-blind mechanical audit; human rule decisions remain separate",
        "bindings": {
            "dataset_sha256": digest(args.dataset),
            "rules_sha256": digest(args.rules),
            "capture_sha256": digest(args.capture),
            "supplement_sha256": [digest(path) for path in args.supplement],
            "ocr_diagnostic_sha256": digest(args.ocr) if args.ocr else None,
        },
        "policy": {
            "provider_outputs_read": False,
            "ocr_is_scoring_evidence": False,
            "page_identity_preferred_anchor_groups": 3,
            "under_three_requires_exception_review": True,
            "all_registered_groups_required": True,
            "stable_text_purpose": "page identity plus representative content",
        },
        "summary": {
            "cases": len(findings),
            "capture_incomplete": sum(row["mechanical_status"] == "capture_incomplete" for row in findings),
            "candidate_keep": sum(row["mechanical_status"] == "candidate_keep" for row in findings),
            "candidate_revision_or_access_blocker": sum(row["mechanical_status"] == "candidate_revision_or_access_blocker" for row in findings),
            "page_identity_under_three": sum(row["page_identity_under_three"] for row in findings),
            "cross_case_collisions": len(collisions),
            "human_decisions_pending": len(findings),
        },
        "cross_case_collisions": collisions,
        "cases": findings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
