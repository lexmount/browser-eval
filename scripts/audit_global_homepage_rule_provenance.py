"""Audit homepage rule literals against saved independent browser observations."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def script_classes(value: str) -> set[str]:
    classes: set[str] = set()
    for character in value:
        codepoint = ord(character)
        if "a" <= character.casefold() <= "z":
            classes.add("latin")
        elif 0x4E00 <= codepoint <= 0x9FFF:
            classes.add("cjk")
        elif 0x3040 <= codepoint <= 0x30FF:
            classes.add("japanese")
        elif 0xAC00 <= codepoint <= 0xD7AF:
            classes.add("korean")
        elif 0x0400 <= codepoint <= 0x04FF:
            classes.add("cyrillic")
        elif 0x0600 <= codepoint <= 0x06FF:
            classes.add("arabic")
    return classes


def captured_visible_strings(capture: dict) -> list[str]:
    values = [capture.get("title", ""), capture.get("visible_text", "")]
    for field in ("headings", "controls", "links"):
        for item in capture.get(field, []):
            if not isinstance(item, dict):
                continue
            for key in ("text", "label", "name", "description", "title", "alt"):
                if isinstance(item.get(key), str):
                    values.append(item[key])
    return [value for value in values if value]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rules", default=ROOT / "research/global-homepage-rules.json", type=Path
    )
    parser.add_argument(
        "--capture",
        default=ROOT / "research/global-homepage-rule-evidence-final-v2-2026-09-11.json",
        type=Path,
    )
    parser.add_argument(
        "--variants",
        default=ROOT / "research/global-homepage-rule-variant-review-2026-09-11.json",
        type=Path,
    )
    parser.add_argument(
        "--visual",
        default=ROOT / "research/global-homepage-visual-spot-check-2026-09-11.json",
        type=Path,
    )
    parser.add_argument(
        "--locale-review",
        default=ROOT / "research/global-homepage-fixed-locale-observations-2026-09-11.json",
        type=Path,
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    rules = json.loads(args.rules.read_text())
    captures = json.loads(args.capture.read_text())
    variants = json.loads(args.variants.read_text())
    visual = json.loads(args.visual.read_text())
    locale_review = json.loads(args.locale_review.read_text())

    captured_by_id = {item["id"]: item for item in captures["observations"]}
    variants_by_domain = {item["domain"]: item for item in variants["observations"]}
    visual_by_domain = {item["domain"]: item for item in visual.get("checks", [])}
    locale_by_id = {item["case_id"]: item for item in locale_review.get("cases", [])}
    rows = []
    for case in rules["cases"]:
        capture = captured_by_id.get(case["id"], {})
        capture_text = normalized("\n".join(captured_visible_strings(capture)))
        visual_literals = {
            normalized(literal)
            for literal in visual_by_domain.get(case["domain"], {}).get(
                "stable_visible_identity", []
            )
        }
        locale_item = locale_by_id.get(case["id"], {})
        locale_literals = {
            normalized(literal)
            for group in locale_item.get("stable_text_groups", [])
            for literal in group
        }
        reviewed_literals = {
            normalized(literal)
            for group in variants_by_domain.get(case["domain"], {}).get(
                "independently_observed_stable_groups", []
            )
            for literal in group
        }
        anchors = case.get("acceptance_contract", {}).get("stable_text_anchors", [])
        fixed_url = locale_item.get("decision") == "change_url_and_contract"
        refreshed_contract = locale_item.get("decision") == "refresh_contract_same_url"
        reviewed_contract = fixed_url or refreshed_contract
        primary_evidence = args.locale_review if reviewed_contract else args.capture
        if fixed_url and case["url"] != locale_item.get("requested_url"):
            raise ValueError(f"{case['id']}: rule URL differs from fixed-locale evidence")
        unsupported = []
        literal_provenance = []
        multilingual_groups = 0
        for anchor in anchors:
            group_scripts: set[str] = set()
            for alternative in anchor.get("alternatives", []):
                literal = normalized(alternative)
                sources = []
                if literal and literal in capture_text:
                    sources.append(str(args.capture.relative_to(ROOT)))
                if literal in visual_literals:
                    sources.append(str(args.visual.relative_to(ROOT)))
                if literal in reviewed_literals:
                    sources.append(str(args.variants.relative_to(ROOT)))
                if literal in locale_literals:
                    sources.append(str(args.locale_review.relative_to(ROOT)))
                supported = bool(literal) and (
                    literal in locale_literals if reviewed_contract else literal in capture_text
                )
                literal_provenance.append({
                    "anchor_id": anchor["id"],
                    "literal": alternative,
                    "primary_supported": supported,
                    "observed_in": sources,
                })
                if not supported:
                    unsupported.append(
                        {"anchor_id": anchor["id"], "literal": alternative}
                    )
                group_scripts.update(script_classes(alternative))
            # A single native-language phrase may legitimately contain a Latin
            # brand token (for example, Yahoo! JAPAN); only alternative spellings
            # in different scripts indicate a flat cross-locale OR contract.
            if len(anchor.get("alternatives", [])) > 1 and len(group_scripts) > 1:
                multilingual_groups += 1
        rows.append(
            {
                "case_id": case["id"],
                "domain": case["domain"],
                "schema": case.get("acceptance_contract", {}).get("schema"),
                "anchor_group_count": len(anchors),
                "multilingual_anchor_group_count": multilingual_groups,
                "flat_locale_mixing_risk": multilingual_groups >= 2,
                "unsupported_literals": unsupported,
                "unsupported_literal_count": len(unsupported),
                "primary_evidence": str(primary_evidence.relative_to(ROOT)),
                "primary_evidence_provider_calls": False,
                "literal_provenance": literal_provenance,
                "evidence_scope": {
                    "capture": str(args.capture.relative_to(ROOT)),
                    "variant_review": str(args.variants.relative_to(ROOT)),
                    "visual_spot_check": str(args.visual.relative_to(ROOT)),
                    "locale_review": str(args.locale_review.relative_to(ROOT)),
                },
            }
        )

    output = {
        "schema_version": 1,
        "purpose": (
            "Mechanical provenance audit only. Unchanged URLs must be supported directly "
            "by the original independent browser capture; changed URLs must be supported "
            "directly by the exact fixed-language URL observation. Supplemental reviews "
            "cannot rescue missing primary evidence. Provider outputs are not consulted."
        ),
        "evidence_files_provider_free": (
            captures.get("capture_contract", {}).get("provider_calls") is False
            and variants.get("provider_calls") is False
            and locale_review.get("provider_calls") is False
        ),
        "limitations": [
            "Substring presence proves observation, not long-term stability.",
            "Script-class detection is a conservative signal for flat locale mixing, not a language classifier.",
            "Unsupported means absent from the declared independent evidence files; it is not proof that the text cannot appear on the live site.",
        ],
        "case_count": len(rows),
        "cases_with_unsupported_literals": sum(
            bool(row["unsupported_literals"]) for row in rows
        ),
        "cases_with_flat_locale_mixing_risk": sum(
            row["flat_locale_mixing_risk"] for row in rows
        ),
        "cases": rows,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
