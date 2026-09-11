"""Audit every failed homepage rule without using provider output as reference.

Measured results select the failed inventory and describe the observed failure.
Rule validity is checked only against provider-free independent browser evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


ANCHOR_ROLES = {
    "youtube.com": ["page_identity", "navigation", "function_control", "function_control", "account_control"],
    "chatgpt.com": ["page_identity", "persistent_prompt", "accessibility_identity", "navigation"],
    "reddit.com": ["section_label", "navigation", "navigation"],
    "bing.com": ["page_identity", "navigation", "navigation", "function_control", "account_control"],
    "gemini.google.com": ["page_identity", "accessibility_identity", "static_hero_copy"],
    "yahoo.com": ["function_control", "navigation", "navigation", "navigation", "section_label"],
    "amazon.com": ["page_identity", "navigation", "navigation"],
    "baidu.com": ["function_control", "navigation", "navigation", "navigation"],
    "naver.com": ["page_identity", "persistent_footer_label", "persistent_footer_link", "persistent_footer_link"],
    "bilibili.com": ["navigation", "navigation", "navigation", "navigation"],
    "dzen.ru": ["function_control", "navigation", "navigation", "navigation"],
    "twitch.tv": ["section_label", "section_label", "section_label"],
    "weather.com": ["navigation", "navigation", "section_label"],
    "fandom.com": ["page_identity", "navigation", "navigation", "navigation", "navigation"],
    "mail.ru": ["page_identity", "navigation", "navigation", "navigation"],
    "spotify.com": ["navigation", "section_label", "section_label", "section_label"],
    "nytimes.com": ["page_identity", "section_label", "persistent_footer_link", "persistent_footer_link"],
    "booking.com": ["page_identity", "navigation", "navigation", "navigation", "function_control"],
    "espn.com": ["page_identity", "section_label", "section_label"],
    "ya.ru": ["function_control", "function_control", "navigation"],
    "indeed.com": ["static_hero_copy", "section_label", "section_label", "function_control"],
    "rakuten.co.jp": ["page_identity", "section_label", "section_label", "navigation"],
    "cnn.com": ["page_identity", "section_label", "section_label", "section_label"],
    "namu.wiki": ["page_identity", "persistent_explanatory_copy", "persistent_disclosure", "help_navigation"],
    "qq.com": ["section_label", "section_label", "publisher_identity", "section_label"],
    "douyin.com": ["navigation", "navigation", "navigation", "navigation"],
    "primevideo.com": ["page_identity", "navigation", "navigation", "navigation"],
    "rutube.ru": ["page_identity", "navigation", "navigation", "navigation", "section_label"],
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    dataset_path = evidence / "dataset.json"
    results_path = evidence / "results.jsonl"
    dataset = json.loads(dataset_path.read_text())
    results = [json.loads(line) for line in results_path.read_text().splitlines()]
    cases = {case["id"]: case for case in dataset["cases"]}
    rules_payload = json.loads((ROOT / "research/global-homepage-rules.json").read_text())
    rules = {case["id"]: case for case in rules_payload["cases"]}
    provenance_payload = json.loads((ROOT / "research/global-homepage-rule-provenance-audit-2026-09-11.json").read_text())
    provenance = {case["case_id"]: case for case in provenance_payload["cases"]}
    locale_payload = json.loads((ROOT / "research/global-homepage-fixed-locale-observations-2026-09-11.json").read_text())
    locale = {case["case_id"]: case for case in locale_payload["cases"]}
    capture_payload = json.loads((ROOT / "research/global-homepage-rule-evidence-final-v2-2026-09-11.json").read_text())
    capture = {case["id"]: case for case in capture_payload["observations"]}

    failed = [row for row in results if not row["metrics"].get("stable_task_pass")]
    failed_domains = {rules[row["case_id"]]["domain"] for row in failed}
    if failed_domains != set(ANCHOR_ROLES):
        raise ValueError("Manual failed-rule review inventory differs from current measured failures")
    if not provenance_payload.get("evidence_files_provider_free"):
        raise ValueError("Independent rule evidence is not provider-free")
    if provenance_payload.get("cases_with_flat_locale_mixing_risk") != 0:
        raise ValueError("At least one current rule still mixes locale alternatives")

    rows = []
    volatile_pattern = re.compile(
        r"(?:[$€£¥]\s*\d|\b\d+(?:[.,]\d+)?\s*(?:views?|articles?|items?|%|off)\b|"
        r"\b(?:20\d{2}|today['’]?s?\s+headline|breaking\s+news:)\b)", re.I
    )
    for measured in failed:
        case_id = measured["case_id"]
        case = cases[case_id]
        rule = rules[case_id]
        domain = rule["domain"]
        roles = ANCHOR_ROLES[domain]
        anchor_groups = rule["anchor_groups"]
        if len(roles) != len(anchor_groups):
            raise ValueError(f"{case_id}: anchor-role review is incomplete")
        source = ROOT / "evaluation" / rule["evidence"]
        source_record = json.loads(source.with_name("evidence.json").read_text())
        prov = provenance[case_id]
        if (source_record.get("provider_output_used") is not False
                or source_record.get("clicks_or_recursive_navigation") is not False
                or prov.get("unsupported_literal_count") != 0
                or prov.get("primary_evidence_provider_calls") is not False):
            raise ValueError(f"{case_id}: independent reference boundary is not satisfied")
        anchors = []
        for group, role in zip(anchor_groups, roles):
            volatile = any(volatile_pattern.search(value) for value in group)
            anchors.append({
                "alternatives": group,
                "semantic_role": role,
                "is_current_item_or_dynamic_value": volatile,
                "review": ("rejected" if volatile else
                           "kept_as_template_identity_navigation_control_or_section_text"),
            })
        if any(item["is_current_item_or_dynamic_value"] for item in anchors):
            raise ValueError(f"{case_id}: volatile current value remains in the stable contract")

        locale_item = locale.get(case_id, {})
        language = (locale_item.get("fixed_language") or locale_item.get("observed_language")
                    or capture[case_id].get("document_lang") or "native_or_undeclared")
        metrics = measured["metrics"]
        final_url = measured.get("final_url")
        requested_host = urlparse(measured["url"]).hostname
        final_host = urlparse(final_url).hostname if final_url else None
        if not measured.get("ok"):
            measured_cause = "provider_route_rejected_or_failed"
        elif final_url and any(token in final_url.lower() for token in ("/login", "/sso", "passport", "account.")):
            measured_cause = "provider_landed_on_non_target_auth_page"
        elif metrics.get("content_chars", 0) < 50:
            measured_cause = "provider_returned_only_title_or_shell"
        elif requested_host and final_host and requested_host != final_host and domain == "yahoo.com":
            measured_cause = "provider_received_different_regional_page_variant"
        else:
            measured_cause = "provider_output_did_not_cover_frozen_contract"
        rows.append({
            "case_id": case_id,
            "domain": domain,
            "url": case["url"],
            "schema": rule["rule_model"],
            "reference_review": {
                "language_contract": language,
                "anchor_groups": anchors,
                "current_headlines_prices_ranks_dates_or_counts_frozen": False,
                "independent_evidence": rule["evidence"],
                "independent_evidence_sha256": sha(source),
                "primary_provenance": prov["primary_evidence"],
                "provider_output_used": False,
                "recursive_navigation_used": False,
                "rule_action": "keep_current_frozen_rule",
            },
            "measured_diagnosis": {
                "cause": measured_cause,
                "request_ok": measured.get("ok"),
                "status_code": measured.get("status_code"),
                "final_url": final_url,
                "stable_anchor_hits": metrics.get("stable_anchor_hits"),
                "stable_anchor_total": metrics.get("stable_anchor_total"),
                "unique_records": metrics.get("stable_unique_record_count"),
                "minimum_records": metrics.get("stable_minimum_records"),
                "used_to_define_or_relax_rule": False,
            },
        })

    output = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Complete rule-validity and failure-cause audit for every failed current homepage case.",
        "policy": {
            "provider_output_may_select_failed_inventory_and_describe_failure": True,
            "provider_output_may_define_or_relax_reference_rule": False,
            "rule_source": "Provider-free independent browser evidence only",
            "volatile_current_members_and_values_are_forbidden": True,
        },
        "bindings": {
            "dataset_sha256": sha(dataset_path),
            "results_sha256": sha(results_path),
            "rule_registry_sha256": sha(ROOT / "research/global-homepage-rules.json"),
            "provenance_audit_sha256": sha(ROOT / "research/global-homepage-rule-provenance-audit-2026-09-11.json"),
        },
        "failed_case_count": len(rows),
        "failed_cases_reviewed": len(rows),
        "unresolved_rule_reviews": 0,
        "cases_with_provider_derived_reference": 0,
        "cases_with_flat_locale_mixing_risk": 0,
        "cases_with_volatile_current_values_in_rules": 0,
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "failed_cases": len(rows), "reviewed": len(rows), "unresolved": 0,
        "provider_derived_rules": 0, "volatile_rule_values": 0,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
