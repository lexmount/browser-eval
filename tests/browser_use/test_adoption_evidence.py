from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def test_public_adoption_counts_are_derived_from_unique_first_party_records():
    payload = json.loads(
        Path("evaluation/browser-use-public/adoption-evidence.json").read_text(encoding="utf-8")
    )
    records = payload["records"]
    pairs = [(row["organization"], row["benchmark_family"]) for row in records]
    assert len(pairs) == len(set(pairs))
    assert all(row["source_type"].startswith("first-party") for row in records)
    assert all(str(row["url"]).startswith("https://") for row in records)

    derived: dict[str, set[str]] = defaultdict(set)
    for row in records:
        derived[row["benchmark_family"]].add(row["organization"])
    declared = {row["benchmark_family"]: row for row in payload["family_counts"]}
    assert set(declared) == set(derived)
    for family, organizations in derived.items():
        assert declared[family]["organization_count"] == len(organizations)
        assert set(declared[family]["organizations"]) == organizations


def test_benchmark_catalog_has_complete_comparable_fields():
    payload = json.loads(
        Path("evaluation/browser-use-public/benchmark-catalog.json").read_text(encoding="utf-8")
    )
    rows = payload["benchmarks"]
    assert {row["category"] for row in rows} == {
        "end_to_end_browser_use",
        "computer_use",
        "gui_grounding",
        "web_research",
        "offline_action_prediction",
    }
    required = {
        "name",
        "version",
        "task_count",
        "environment",
        "online",
        "local_deployment",
        "account_requirement",
        "input_and_trajectory",
        "reference_trajectory",
        "final_acceptance",
        "scoring",
        "difficulty",
        "license",
        "cost",
        "stability",
        "known_issues",
        "source_urls",
    }
    assert all(required <= row.keys() for row in rows)
    assert all(
        urls and all(url.startswith("https://") for url in urls)
        for urls in (row["source_urls"] for row in rows)
    )
    assert [
        row["name"]
        for row in sorted(
            (row for row in rows if row["engineering_rank"] is not None),
            key=lambda row: row["engineering_rank"],
        )
    ] == ["WebArena-Verified", "Online-Mind2Web", "BU Bench V1"]
