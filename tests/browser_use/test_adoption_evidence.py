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
