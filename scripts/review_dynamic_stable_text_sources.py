#!/usr/bin/env python3
"""Build a provider-blind stable-text adjudication from source observations.

Only independently captured source-page observations are read.  Provider
outputs, scores, and verdicts are deliberately outside this command's inputs.
The automatic decision is intentionally narrow: retain meaningful ordinary
text that appears in every successful observation and remains in the stable
outer portion of the document (page chrome, headings, or footer).  Volatile
numbers, prices, dates, and current record links stay visible in the audit but
are not promoted to permanent anchors.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


# Keep this band deliberately narrow.  A wide "edge" silently captures the
# first and last content cards on list pages, which are still volatile records.
EDGE_NODES = 20
LINK_SCHEMAS = {"named_link", "linked_product"}
LEGAL_HINT = re.compile(
    r"(?i)(copyright|privacy|terms|license|licence|contact|about|举报|许可证|备案|协议|联系我们)"
)
VOLATILE_HINT = re.compile(
    r"(?i)(?:^|\s)(?:\d{1,2}[:/]\d{1,2}|\d{4}[-/]\d{1,2}|\d+(?:\.\d+)?%|"
    r"[$£€¥]\s*\d|\d+(?:\.\d+)?\s*(?:万|億|million|views?|likes?|comments?))(?:\s|$)"
)
COPYRIGHT_YEAR = re.compile(r"(?i)(?:copyright|©).*\b20\d{2}\b")
PAGINATION_HINT = re.compile(
    r"(?i)^(?:next|previous|prev|older|newer|下一页|上一页|下页|上页|次へ|前へ)$"
)


def canonical(value: str) -> str:
    return " ".join(str(value or "").split())


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def meaningful(text: str) -> bool:
    if len(text) < 2 or len(text) > 600:
        return False
    letters = sum(character.isalpha() or "\u3400" <= character <= "\u9fff" for character in text)
    return letters >= 2 and letters / max(1, len(text)) >= 0.2


def likely_volatile(text: str) -> bool:
    if COPYRIGHT_YEAR.search(text):
        return True
    if LEGAL_HINT.search(text):
        return False
    digits = sum(character.isdigit() for character in text)
    return bool(VOLATILE_HINT.search(text) or digits / max(1, len(text)) > 0.22)


def row_positions(observation: dict) -> dict[str, dict]:
    rows = observation.get("ordinary_text_nodes") or []
    positions: dict[str, dict] = {}
    for index, row in enumerate(rows):
        text = canonical(row.get("text", ""))
        if not text:
            continue
        item = positions.setdefault(text, {
            "first": index, "last": index, "count": 0,
            "linked": False, "landmarks": set(), "tags": set(),
        })
        item["first"] = min(item["first"], index)
        item["last"] = max(item["last"], index)
        item["count"] += 1
        item["linked"] = item["linked"] or bool(row.get("linked"))
        item["landmarks"].add(row.get("landmark") or "body")
        item["tags"].add(row.get("tag") or "")
    total = len(rows)
    for item in positions.values():
        item["edge"] = item["first"] < EDGE_NODES or item["last"] >= max(0, total - EDGE_NODES)
    return positions


def existing_texts(contract: dict) -> dict[str, dict]:
    result = {}
    for anchor in contract.get("stable_text_anchors", []):
        for alternative in anchor.get("alternatives", []):
            result[canonical(alternative).casefold()] = anchor
    return result


def stable_id(text: str) -> str:
    return "source-stable-" + hashlib.sha256(text.casefold().encode()).hexdigest()[:12]


def adjudicate(case: dict, observed: dict | None, context: dict | None) -> tuple[list[dict], dict]:
    contract = case.get("acceptance_contract") or {}
    previous = contract.get("stable_text_anchors", [])
    if not contract.get("schema"):
        return previous, {
            "case_id": case["id"], "schema": contract.get("schema"),
            "decision": "unchanged_non_link_contract", "anchors": previous,
        }
    if not observed or not observed.get("all_observations_complete"):
        # Structured list cases remain fully testable through record ownership,
        # count, uniqueness, host, and path rules.  A prior literal must not be
        # presented as newly verified stable text when the declared three-load
        # source observation did not complete; absence of verified text is
        # preferable to padding the contract.
        anchors = [] if contract.get("schema") in LINK_SCHEMAS else previous
        return anchors, {
            "case_id": case["id"], "schema": contract.get("schema"),
            "decision": ("removed_unverified_link_text_observation_incomplete"
                         if contract.get("schema") in LINK_SCHEMAS
                         else "retained_previous_source_evidence_observation_incomplete"),
            "anchors": anchors,
            "removed_previous_anchors": previous if not anchors else [],
            "observation_complete": False,
        }
    observations = [row for row in observed["observations"] if row.get("status") == "success"]
    positions = [row_positions(row) for row in observations]
    context_usable = bool(
        context
        and context.get("status") == "success"
        and isinstance(context.get("http_status"), int)
        and 200 <= context["http_status"] < 400
        and context.get("scroll_x") == 0
        and context.get("scroll_y") == 0
    )
    context_rows: dict[str, list[dict]] = {}
    for row in (context or {}).get("rows", []):
        context_rows.setdefault(canonical(row.get("text", "")), []).append(row)
    common = set(positions[0])
    for row in positions[1:]:
        common &= set(row)
    previous_by_text = existing_texts(contract)
    decisions = []
    selected: dict[str, dict] = {}
    for text in sorted(common, key=lambda value: (value.casefold(), value)):
        key = text.casefold()
        old = previous_by_text.get(key)
        edge_every_time = all(row[text]["edge"] for row in positions)
        linked = any(row[text]["linked"] for row in positions)
        classified = context_rows.get(text, [])
        only_target_record = (
            contract.get("schema") in LINK_SCHEMAS
            and bool(classified)
            and all(row.get("target_record") for row in classified)
        )
        # A current headline can repeat across three closely spaced loads and
        # can also sit at a document edge.  It is still a volatile record, not
        # stable page text.  The supplementary source load records enough DOM
        # context to reject common headline/card shapes without consulting the
        # evaluated provider output.
        only_current_content_record = bool(classified) and all(
            (
                row.get("linked")
                and (
                    row.get("tag") in {"h3", "h4"}
                    or row.get("record_container") in {"article", "figcaption"}
                )
            )
            or (
                contract.get("schema") not in LINK_SCHEMAS
                and row.get("landmark") in {"body", "main"}
                and bool(row.get("record_container"))
            )
            for row in classified
        )
        long_main_promo = len(text) > 75 and bool(classified) and all(
            row.get("landmark") == "main"
            and (row.get("linked") or row.get("tag") != "p")
            for row in classified
        )
        eligible_non_link_chrome = (
            contract.get("schema") in LINK_SCHEMAS
            or (
                bool(classified)
                and all(
                    row.get("landmark") in {"nav", "header", "footer", "aside"}
                    or (
                        row.get("landmark") == "body"
                        and (
                            row.get("tag") in {"h1", "h2"}
                            or (
                                row.get("tag") == "a"
                                and row.get("linked")
                                and not row.get("record_container")
                            )
                        )
                    )
                    for row in classified
                )
            )
        )
        pagination_control = bool(classified) and all(
            row.get("linked")
            and (
                PAGINATION_HINT.fullmatch(text)
                or re.search(r"(?:^|/)page/\d+(?:/|$)", urlsplit(row.get("href") or "").path)
                or re.search(r"(?:^|[?&])page=\d+(?:&|$)", urlsplit(row.get("href") or "").query)
            )
            for row in classified
        )
        stable_landmark = bool(classified) and all(
            row.get("landmark") in {"nav", "header", "footer", "aside"}
            for row in classified
        )
        stable_outer_text = edge_every_time or stable_landmark
        if only_target_record:
            keep, reason = False, "text_belongs_only_to_declared_target_records"
        elif only_current_content_record:
            keep, reason = False, "current_headline_or_content_card"
        elif long_main_promo:
            keep, reason = False, "current_main_content_or_promotion"
        elif old:
            keep, reason = True, "previous_anchor_confirmed_in_three_source_loads"
        elif pagination_control:
            keep, reason = False, "pagination_or_navigation_control"
        elif not eligible_non_link_chrome:
            keep, reason = False, "structured_record_or_non_chrome_text"
        elif not context_usable:
            keep, reason = False, "later_source_context_load_not_usable"
        elif not classified:
            keep, reason = False, "not_present_in_later_source_context_check"
        elif not meaningful(text):
            keep, reason = False, "not_meaningful_ordinary_text"
        elif likely_volatile(text):
            keep, reason = False, "volatile_number_price_or_date"
        elif not stable_outer_text:
            keep, reason = False, "current_body_or_record_text_not_stable_page_chrome"
        else:
            keep, reason = True, "stable_outer_document_text_in_three_source_loads"
        decisions.append({
            "text": text, "decision": "keep" if keep else "exclude", "reason": reason,
            "edge_in_every_observation": edge_every_time, "linked": linked,
            "only_target_record": only_target_record,
            "only_current_content_record": only_current_content_record,
            "long_main_promo": long_main_promo,
            "eligible_non_link_chrome": eligible_non_link_chrome,
            "pagination_control": pagination_control,
            "stable_landmark": stable_landmark,
            "landmarks": sorted(set().union(*(row[text]["landmarks"] for row in positions))),
            "tags": sorted(set().union(*(row[text]["tags"] for row in positions))),
        })
        if keep:
            anchor = deepcopy(old) if old else {
                "id": stable_id(text), "alternatives": [text],
            }
            if contract.get("schema") == "named_link":
                anchor["match_scope"] = "non_record_lines"
            selected[key] = anchor
    # Previously frozen source evidence is not silently deleted.  A missing
    # historical anchor remains visible and becomes an explicit unresolved row.
    preserved_historical = []
    for anchor in previous:
        if not any(canonical(value).casefold() in selected for value in anchor.get("alternatives", [])):
            preserved_historical.append(anchor)
            selected["previous:" + str(anchor.get("id"))] = deepcopy(anchor)
    anchors = sorted(selected.values(), key=lambda row: (row.get("id", ""), row["alternatives"][0].casefold()))
    return anchors, {
        "case_id": case["id"], "schema": contract.get("schema"),
        "decision": "source_only_stable_text_review",
        "observation_complete": True,
        "candidate_count": len(common), "kept_count": len(anchors),
        "previous_count": len(previous),
        "preserved_historical_anchors_not_reobserved_as_single_text_node": preserved_historical,
        "decisions": decisions, "anchors": anchors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--contexts", type=Path, action="append", required=True)
    parser.add_argument("--supplement", type=Path, action="append", default=[])
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--dataset-output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.audit_output, args.dataset_output):
        if path.exists():
            parser.error(f"output exists: {path}")
    dataset_raw = args.dataset.read_bytes()
    dataset = json.loads(dataset_raw)
    source_raw = args.observations.read_bytes()
    source = json.loads(source_raw)
    if source.get("dataset_sha256") != digest(dataset_raw):
        parser.error("source observations are bound to a different dataset")
    observed = dict(source.get("cases") or {})
    context_inputs = []
    context_cases = {}
    for path in args.contexts:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if (payload.get("dataset_sha256") != digest(dataset_raw)
                or payload.get("source_observations_sha256") != digest(source_raw)):
            parser.error(f"contexts are bound to different source observations: {path}")
        overlap = set(context_cases) & set(payload.get("cases") or {})
        if overlap:
            parser.error(f"duplicate context cases: {sorted(overlap)}")
        context_cases.update(payload.get("cases") or {})
        context_inputs.append({"sha256": digest(raw), "cases": len(payload.get("cases") or {})})
    supplements = []
    for path in args.supplement:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if payload.get("dataset_sha256") != digest(dataset_raw):
            parser.error(f"supplement is bound to a different dataset: {path}")
        supplements.append({"sha256": digest(raw), "cases": sorted((payload.get("cases") or {}).keys())})
        for identifier, row in (payload.get("cases") or {}).items():
            if row.get("all_observations_complete"):
                observed[identifier] = row
    revised = deepcopy(dataset)
    audit_rows = []
    for case in revised["cases"]:
        anchors, audit = adjudicate(
            case, observed.get(case["id"]), context_cases.get(case["id"]))
        if (case.get("acceptance_contract") or {}).get("schema"):
            case["acceptance_contract"]["stable_text_anchors"] = anchors
        audit_rows.append(audit)
    revised_raw = json_bytes(revised)
    audit = {
        "schema_version": 1,
        "kind": "dynamic_100_provider_blind_stable_text_readjudication",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_dataset_sha256": digest(dataset_raw),
        "source_observations_sha256": digest(source_raw),
        "context_inputs": context_inputs,
        "supplements": supplements,
        "provider_output_used": False,
        "interaction_boundary": "initial automatic load only; no click, input, scroll, search, or pagination",
        "selection_policy": {
            "minimum_observations": 3,
            "duplicate_text_count": 1,
            "ordinary_text_only": True,
            "identifying_power_required": False,
            "edge_nodes": EDGE_NODES,
            "volatile_values_excluded": True,
            "non_success_source_pages_excluded": True,
            "current_headlines_and_content_cards_excluded": True,
        },
        "cases": audit_rows,
        "output_dataset_sha256": digest(revised_raw),
    }
    args.dataset_output.write_bytes(revised_raw)
    args.audit_output.write_bytes(json_bytes(audit))
    print(json.dumps({
        "dataset_sha256": digest(revised_raw),
        "audit_sha256": digest(args.audit_output.read_bytes()),
        "structured_cases": sum(bool(row.get("schema")) for row in audit_rows),
        "incomplete": [row["case_id"] for row in audit_rows if row.get("observation_complete") is False],
        "historical_anchor_preservations": [
            row["case_id"] for row in audit_rows
            if row.get("preserved_historical_anchors_not_reobserved_as_single_text_node")
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
