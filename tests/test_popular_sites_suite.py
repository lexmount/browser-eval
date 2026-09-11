# ruff: noqa: E501 -- assertions retain the complete contract field names
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text())


def test_popular_sites_suite_has_exact_frozen_inventory():
    dataset = load("evaluation/popular-sites-dataset.json")
    rules = load("research/popular-sites-rules.json")
    assert len(dataset["cases"]) == 100
    assert len(rules["cases"]) == 100
    assert rules["counts"] == {
        "inherited_formal_results": 52,
        "new_provider_calls": 48,
        "total": 100,
    }
    assert dataset["comparison"]["require_independent_result_review"] is True
    assert [item["id"] for item in dataset["cases"]] == [item["id"] for item in rules["cases"]]


def test_only_48_cases_are_new_provider_calls():
    rules = load("research/popular-sites-rules.json")
    inherited = [item for item in rules["cases"] if item["cohort"] == "inherited_global_homepage"]
    new = [item for item in rules["cases"] if item["cohort"].startswith("new_")]
    assert len(inherited) == 52
    assert len(new) == 48


def test_sites_are_unique_and_duplicate_legacy_amazon_reddit_are_absent():
    dataset = load("evaluation/popular-sites-dataset.json")
    domains = []
    for case in dataset["cases"]:
        domain = case.get("source_benchmark", {}).get("requested_ranked_domain")
        domains.append(domain or urlparse(case["url"]).hostname.removeprefix("www."))
    assert len(domains) == len(set(domains)) == 100
    ids = {case["id"] for case in dataset["cases"]}
    assert "challenge-07" not in ids
    assert "challenge-08" not in ids


def test_new_named_link_stable_anchors_cannot_be_satisfied_by_record_titles():
    rules = load("research/popular-sites-rules.json")
    for item in rules["cases"]:
        if item["cohort"] != "new_china_ranked_homepage":
            continue
        contract = item["acceptance_contract"]
        if contract["schema"] == "named_link":
            assert contract["minimum_records"] == 3
            assert all(
                anchor["match_scope"] == "non_record_lines"
                for anchor in contract["stable_text_anchors"]
            )
