#!/usr/bin/env python3
# ruff: noqa: E501 -- machine-readable policy prose is intentionally kept whole
"""Build the China-ranked homepage candidate pool without provider calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
RANKING = ROOT / "research/semrush-china-top-100-2026-07.json"
HOMEPAGES = ROOT / "evaluation/global-homepages-dataset.json"
LEGACY = ROOT / "evaluation/dataset.json"
OUTPUT = ROOT / "research/china-homepage-candidates-2026-09-11.json"


ADULT_DOMAINS = {"pornhub.com"}


def host(url: str) -> str:
    value = (urlparse(url).hostname or "").lower()
    return value.removeprefix("www.")


def legacy_site_domain(case: dict) -> str:
    hostname = host(case["url"])
    overrides = {
        "search.jd.com": "jd.com",
        "movie.douban.com": "douban.com",
    }
    return overrides.get(hostname, hostname)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ranking = json.loads(RANKING.read_text())
    homepage_payload = json.loads(HOMEPAGES.read_text())
    legacy_payload = json.loads(LEGACY.read_text())

    homepage_domains = {
        case["source_benchmark"]["requested_ranked_domain"] for case in homepage_payload["cases"]
    }
    legacy_cases = [case for case in legacy_payload["cases"] if case.get("track") == "challenge"]
    legacy_domains = {legacy_site_domain(case) for case in legacy_cases}
    definite_overlap = sorted(homepage_domains & legacy_domains)
    existing_domains = homepage_domains | legacy_domains

    candidates = []
    exclusions = []
    for item in ranking["domains"]:
        domain = item["domain"]
        record = {"china_rank": item["rank"], "domain": domain, "url": f"https://{domain}/"}
        if domain in ADULT_DOMAINS:
            exclusions.append({**record, "reason_code": "adult_content_not_visited"})
        elif domain in existing_domains:
            exclusions.append({**record, "reason_code": "existing_site_domain"})
        else:
            candidates.append(record)

    result = {
        "schema_version": 1,
        "purpose": "Policy-filtered China Top 100 candidates awaiting independent initial-page observation",
        "source": {
            "ranking_file": str(RANKING.relative_to(ROOT)),
            "ranking_file_sha256": sha256(RANKING),
            "homepage_dataset": str(HOMEPAGES.relative_to(ROOT)),
            "homepage_dataset_sha256": sha256(HOMEPAGES),
            "legacy_dataset": str(LEGACY.relative_to(ROOT)),
            "legacy_dataset_sha256": sha256(LEGACY),
        },
        "count_reconciliation": {
            "homepage_case_count": len(homepage_payload["cases"]),
            "legacy_popular_case_count": len(legacy_cases),
            "definite_overlapping_site_domains": definite_overlap,
            "unique_existing_site_domain_count": len(existing_domains),
            "target_unique_site_domain_count": 100,
            "new_unique_site_domains_required": 100 - len(existing_domains),
            "note": "The stated 52 + 10 baseline contains Amazon and Reddit twice, so 40 new unique domains are required for a strict 100-site union.",
        },
        "selection_policy": {
            "rank_order_preserved": True,
            "ranking_metadata_used_for_scoring": False,
            "adult_domains_not_visited": True,
            "login_blank_error_captcha_and_duplicate_final_pages_must_be_excluded_after_observation": True,
            "target_count_is_a_post_eligibility_requirement": True,
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
        "upstream_exclusions": exclusions,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Built {len(candidates)} candidates; strict target needs {100 - len(existing_domains)} new domains"
    )


if __name__ == "__main__":
    main()
