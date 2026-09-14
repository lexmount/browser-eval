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
        "cases": 100,
        "methods": 12,
        "fresh_formal_results": 1200,
        "provider_results_reused": 0,
    }
    assert dataset["comparison"]["require_independent_result_review"] is True
    assert len(dataset["coverage_plan"]["challenge"]) == 12
    assert dataset["comparison"]["body_reference_case_count"] == 0
    assert dataset["comparison"]["body_metrics"] == "unavailable"
    assert [item["id"] for item in dataset["cases"]] == [item["id"] for item in rules["cases"]]


def test_all_1200_results_are_fresh_and_cohorts_cover_100_cases():
    rules = load("research/popular-sites-rules.json")
    assert rules["counts"]["provider_results_reused"] == 0
    assert rules["counts"]["fresh_formal_results"] == 1200
    assert {item["cohort"] for item in rules["cases"]} == {
        "global_homepage",
        "legacy_popular_list",
        "china_ranked_homepage",
        "china_ranked_reserve_replacement",
    }


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
        if item["cohort"] != "china_ranked_homepage":
            continue
        contract = item["acceptance_contract"]
        if contract["schema"] == "named_link":
            assert contract["minimum_records"] == 3
            assert all(
                anchor["match_scope"] == "non_record_lines"
                for anchor in contract["stable_text_anchors"]
            )


def test_accepted_stability_revisions_are_frozen_without_generic_not_found_block():
    dataset = load("evaluation/popular-sites-dataset.json")
    cases = {case["id"]: case for case in dataset["cases"]}

    expected = {
        "global-homepage-010-bing-com": ["Microsoft Bing", "Images", "Maps", "Sign in"],
        "global-homepage-020-naver-com": ["NAVER", "공지사항", "Partners", "Developers"],
        "global-homepage-080-cnn-com": ["CNN", "MORE TOP STORIES"],
        "china-homepage-008-doubao-com": ["豆包", "有什么我能帮你的吗？", "对话", "图像生成"],
        "china-homepage-080-yandex-com": ["Yandex", "Search with Yandex AI", "Maps"],
    }
    for case_id, anchors in expected.items():
        case = cases[case_id]
        assert [
            item["alternatives"][0]
            for item in case["acceptance_contract"]["stable_text_anchors"]
        ] == anchors

    assert "Not Found" not in cases["china-homepage-063-sina-com-cn"]["forbidden_any"]
    assert "Not Found" in cases["global-homepage-001-google-com"]["forbidden_any"]
    assert "Not Found" in cases["china-homepage-008-doubao-com"]["forbidden_any"]


def test_locale_corrections_and_replacements_are_exact():
    dataset = load("evaluation/popular-sites-dataset.json")
    cases = {case["id"]: case for case in dataset["cases"]}
    assert cases["global-homepage-038-samsung-com"]["url"] == "https://www.samsung.com/us/"
    assert [
        item["alternatives"]
        for item in cases["china-homepage-081-elsevier-com"]["acceptance_contract"][
            "stable_text_anchors"
        ]
    ] == [
        ["Elsevier"],
        ["Academic & Government", "科研学术"],
        ["Health", "医疗健康"],
        ["Publish with us", "通过我们出版", "通过Elsevier出版"],
    ]
    assert "global-homepage-047-booking-com" not in cases
    assert "global-homepage-088-primevideo-com" not in cases
    assert "china-homepage-083-grok-com" in cases
    assert "china-homepage-085-shopify-com" in cases


def test_fresh_execution_contract_is_hash_bound():
    dataset = load("evaluation/popular-sites-dataset.json")
    rules = load("research/popular-sites-rules.json")
    contract_path = ROOT / "research/popular-sites-12-method-contract.json"
    import hashlib

    digest = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    assert dataset["comparison"]["execution_contract_sha256"] == digest
    assert rules["execution_contract"]["sha256"] == digest
    assert dataset["comparison"]["runs_per_case"] == 1
    assert dataset["comparison"]["timeout_seconds"] == 45
    assert dataset["comparison"]["concurrency"] == 1
    assert dataset["comparison"]["methods_run_in_parallel"] is True


def test_formal_batches_are_frozen_before_preflight():
    contract = load("research/popular-sites-12-method-contract.json")
    batches = contract["execution"]["formal_batches"]
    assert [item["batch"] for item in batches] == [1, 2]
    assert [item["kind"] for item in batches] == ["local", "hosted"]
    flattened = [provider for item in batches for provider in item["providers"]]
    assert len(batches[0]["providers"]) == len(batches[1]["providers"]) == 6
    assert len(flattened) == len(set(flattened)) == 12
    assert set(flattened) == set(contract["providers"])
    assert contract["execution"]["diagnostic_preflight"]["case_ids"] == [
        "global-homepage-038-samsung-com",
        "global-homepage-006-reddit-com",
    ]
