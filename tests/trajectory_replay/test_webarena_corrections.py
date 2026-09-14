from __future__ import annotations

import copy
import json

import pytest

from harness.browser_use.runner import _configure_benchmark
from harness.trajectory_replay.webarena_corrections import (
    CorrectedReplayContract,
    apply_correction,
    corrected_task_ids,
    load_corrections,
    normalize_agent_response_for_evaluator,
)


@pytest.fixture(scope="module")
def benchmark():
    return _configure_benchmark()


@pytest.fixture(scope="module")
def catalog():
    return load_corrections()


def _dump_eval(contract: CorrectedReplayContract, index: int) -> dict:
    return contract.task.eval[index].model_dump(mode="json")


def test_catalog_is_replay_only_and_has_exact_reviewed_scope(catalog) -> None:
    payload = json.loads(catalog.source_path.read_text(encoding="utf-8"))
    assert payload["historical_browser_use_scores_immutable"] is True
    assert "Replay" in payload["scope"]
    assert catalog.upstream["package_version"] == "1.2.3"
    assert len(catalog.upstream["dataset_sha256"]) == 64
    assert set(catalog.record_sha256s) == set(catalog.records)
    assert len(set(catalog.record_sha256s.values())) == len(catalog.records)
    assert corrected_task_ids(catalog) == (279, 339, 448, 475, 510, 528, 547, 676, 699)


def test_apply_is_pure_traceable_and_fails_closed(benchmark, catalog) -> None:
    task = benchmark.get_task(279)
    before = task.model_dump(mode="json")
    applied = apply_correction(task, catalog)

    assert task.model_dump(mode="json") == before
    assert applied.task is not task
    assert applied.original_task_sha256 != applied.corrected_task_sha256
    assert len(applied.correction_source_sha256) == 64
    assert applied.correction_source_sha256 == catalog.record_sha256s[279]
    assert applied.correction_source_sha256 != catalog.source_sha256
    assert applied.correction_id.startswith("wv-279-")

    changed = copy.deepcopy(before)
    changed["revision"] = 3
    with pytest.raises(ValueError, match="expected official revision 2"):
        apply_correction(type(task).model_validate(changed), catalog)

    changed = copy.deepcopy(before)
    changed["eval"][0]["expected"]["retrieved_data"][0]["names"][9] = "upstream changed"
    with pytest.raises(ValueError, match="precondition failed"):
        apply_correction(type(task).model_validate(changed), catalog)


def test_279_corrects_only_drifted_reference_title(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(279), catalog)
    names = _dump_eval(applied, 0)["expected"]["retrieved_data"][0]["names"]
    assert names[9] == "Sony WI-C300 Wireless In-Ear Headphones, Red (WIC300/R))"
    assert len(names) == 12


def test_339_uses_canonical_issues_route_and_filters_before_recency(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(339), catalog)
    assert _dump_eval(applied, 1)["expected"]["url"] == (
        "__GITLAB__/a11yproject/a11yproject.com/-/issues"
    )
    assert applied.replay_assertions[0]["policy"].startswith("match_expected_url")
    assert _dump_eval(applied, 2)["last_event_only"] is False
    assert _dump_eval(applied, 2)["expected"]["post_data"] == {
        "$.variables.state": "opened",
        "$.variables.labelName": "bug",
    }


def test_agent_response_normalization_removes_unsupported_null_metadata() -> None:
    assert normalize_agent_response_for_evaluator(
        {
            "task_type": "NAVIGATE",
            "status": "SUCCESS",
            "retrieved_data": None,
            "error_details": None,
        }
    ) == {
        "task_type": "NAVIGATE",
        "status": "SUCCESS",
        "retrieved_data": None,
    }


def test_475_treats_omitted_checkbox_as_false_and_checks_empty_repo(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(475), catalog)
    post_data = _dump_eval(applied, 1)["expected"]["post_data"]
    assert "project[initialize_with_readme]" not in post_data
    assert applied.replay_assertions[0]["empty_repository"] is True


def test_448_matches_the_profile_save_event_before_recency(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(448), catalog)
    evaluator = _dump_eval(applied, 1)
    assert evaluator["expected"]["url"] == "__GITLAB__/-/profile"
    assert evaluator["expected"]["post_data"] == {
        "user[website_url]": "https://egg.tart.com"
    }
    assert evaluator["last_event_only"] is False


def test_510_freezes_selection_order_and_product(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(510), catalog)
    assert "least expensive" in applied.task.intent
    assert "If prices tie" in applied.task.intent
    assert applied.task.instantiation_dict["target_product_id"] == "75640"
    assert _dump_eval(applied, 1)["expected"]["post_data"]["product"] == "75640"
    assert len(applied.task.eval) == 4
    assert all(
        (item.get("expected", {}).get("response_content") or {}).get("items_qty") is None
        for item in applied.task.model_dump(mode="json")["eval"]
    )


def test_528_uses_final_dom_state_and_forbids_real_submission(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(528), catalog)
    network = [_dump_eval(applied, index) for index in range(1, len(applied.task.eval))]
    assert len(network) == 1
    assert network[0]["expected"]["url"] == "__SHOPPING__/contact/index/post"
    assert network[0]["should_not_exist"] is True
    assertion = applied.replay_assertions[0]
    assert assertion["kind"] == "final_dom_form_state"
    assert assertion["fields"]["textarea[name='comment']"].startswith("I need a refund")
    assert assertion["forbid_network_event"]["http_method"] == "POST"


def test_547_removes_redundant_attribute_save_but_keeps_variation_save(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(547), catalog)
    network_urls = [
        item["expected"]["url"]
        for item in (_dump_eval(applied, index) for index in range(1, len(applied.task.eval)))
    ]
    assert not any("product_attribute/save" in url for url in network_urls)
    assert any("catalog/product/save/id/1130" in url for url in network_urls)
    assert applied.replay_assertions == ()


def test_676_selects_matching_filter_xhr_instead_of_main_navigation(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(676), catalog)
    evaluator = _dump_eval(applied, 1)
    assert evaluator["last_event_only"] is False
    assert evaluator["expected"]["query_params"]["filters[status]"] == ["fraud"]
    assert applied.replay_assertions[0]["policy"].startswith("match_expected_url")


def test_699_checks_browser_form_array_fields_and_all_registered_customer_groups(
    benchmark, catalog
) -> None:
    applied = apply_correction(benchmark.get_task(699), catalog)
    evaluator = _dump_eval(applied, 1)
    assert evaluator["post_data_schema"] is None
    assert evaluator["expected"]["post_data"] == {
        "name": "spring sale",
        "website_ids[0]": "1",
        "customer_group_ids[0]": "1",
        "customer_group_ids[1]": "2",
        "customer_group_ids[2]": "3",
        "simple_action": "by_percent",
        "discount_amount": 20,
    }
