from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from harness.browser_use.runner import _configure_benchmark
from harness.trajectory_replay.webarena_corrections import (
    CorrectedReplayContract,
    apply_correction,
    corrected_task_ids,
    evaluate_corrected_contract,
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
    assert corrected_task_ids(catalog) == (225, 279, 448, 510, 528, 547, 699)
    assert catalog.records[448]["regression_ids"] == [
        "tr-448-302-success",
        "tr-448-200-json-success",
        "tr-448-200-json-failure",
        "tr-448-wrong-website",
        "tr-448-canceled-request",
        "tr-448-reject-correct-then-wrong",
        "tr-448-accept-wrong-then-correct",
    ]


def test_448_scoring_revision_is_bound_to_correction_and_regressions(catalog) -> None:
    root = Path(__file__).resolve().parents[2]
    catalog = load_corrections(root / "evaluation/trajectory-replay/webarena-verified/revisions/task-corrections-20260918.json")
    receipt = json.loads(
        (root / "research/trajectory-replay-task448-scoring-revision.json").read_text(
            encoding="utf-8"
        )
    )
    audit = root / "research/trajectory-replay-task448-response-variant-audit.json"
    assert receipt["task_id"] == 448
    assert receipt["provider_call_count"] == 0
    # Historical revision receipts bind the catalog as it existed at release;
    # later independent corrections must not rewrite that provenance.
    assert len(receipt["revised_correction_catalog_sha256"]) == 64
    assert receipt["revised_correction_record_sha256"] == catalog.record_sha256s[448]
    assert receipt["regression_ids"] == catalog.records[448]["regression_ids"]
    assert receipt["evidence_audit_sha256"] == hashlib.sha256(audit.read_bytes()).hexdigest()
    assert receipt["result_revision"]["new_provider_call"] is False


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


def test_final_event_revision_binds_current_and_historical_catalogs(catalog):
    root = Path(__file__).resolve().parents[2]
    receipt = json.loads((root / "research/trajectory-replay-task448-final-event-revision.json").read_text())
    predecessor = load_corrections(root / receipt["predecessor_catalog_path"])
    assert receipt["predecessor_correction_catalog_sha256"] == predecessor.source_sha256
    assert receipt["predecessor_correction_record_sha256"] == predecessor.record_sha256s[448]
    assert receipt["revised_correction_catalog_sha256"] == catalog.source_sha256
    assert receipt["revised_correction_record_sha256"] == catalog.record_sha256s[448]
    assert receipt["regression_ids"] == catalog.records[448]["regression_ids"]
    assert receipt["provider_call_count"] == 0


def test_archived_catalog_reproduces_all_unchanged_task_contracts(benchmark, catalog):
    archived = load_corrections(
        Path("evaluation/trajectory-replay/webarena-verified/revisions/task-corrections-20260918.json"))
    for task_id in corrected_task_ids(archived):
        previous = apply_correction(benchmark.get_task(task_id), archived)
        current = apply_correction(benchmark.get_task(task_id), catalog)
        if task_id == 448:
            assert previous.corrected_task_sha256 != current.corrected_task_sha256
        else:
            assert previous.corrected_task_sha256 == current.corrected_task_sha256



def test_279_corrects_title_and_omitted_bluetooth_model(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(279), catalog)
    names = _dump_eval(applied, 0)["expected"]["retrieved_data"][0]["names"]
    assert "Sony WI-C300 Wireless In-Ear Headphones, Red (WIC300/R))" in names
    assert len(names) == 13
    assert sum("WI-1000XM2" in name for name in names) == 1


@pytest.mark.parametrize(
    "mutation,score",
    [("complete", 1.0), ("omitted", 0.0), ("wired", 0.0)],
    ids=[
        "tr-279-complete-thirteen-products",
        "tr-279-reject-omitted-product",
        "tr-279-reject-wired-product",
    ],
)
def test_279_complete_catalog_scores_both_directions(tmp_path, benchmark, catalog, mutation, score):
    contract = apply_correction(benchmark.get_task(279), catalog)
    response = copy.deepcopy(_dump_eval(contract, 0)["expected"])
    names = response["retrieved_data"][0]["names"]
    if mutation == "omitted":
        names[:] = [name for name in names if "WI-1000XM2" not in name]
    elif mutation == "wired":
        names.append("SONY MDR-Z1R WW2 Signature, Hi-Res Headphone, Black (International Version)")
    trace = tmp_path / "catalog.har"
    trace.write_text(
        json.dumps(
            {
                "log": {
                    "version": "1.2",
                    "creator": {"name": "control", "version": "1"},
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": "http://example.test/",
                                "headers": [],
                            },
                            "response": {
                                "status": 200,
                                "headers": [],
                                "cookies": [],
                                "content": {"mimeType": "text/html", "text": ""},
                            },
                        }
                    ],
                }
            }
        )
    )
    result = evaluate_corrected_contract(
        benchmark, contract, agent_response=response, network_trace=trace
    )
    assert float(result.score) == score


def test_225_corrects_unverifiable_brand_scope_from_independent_catalog(benchmark, catalog) -> None:
    original = benchmark.get_task(225)
    corrected = apply_correction(original, catalog)
    assert "brush from sephora" in original.intent
    assert "titles contain both Sephora and Brush" in corrected.task.intent
    receipt = catalog.records[225]["reference_receipt"]
    reference_path = Path(__file__).resolve().parents[2] / receipt["path"]
    assert hashlib.sha256(reference_path.read_bytes()).hexdigest() == receipt["sha256"]
    source = json.loads(reference_path.read_text(encoding="utf-8"))
    assert corrected.task.eval[0].expected.model_dump(mode="json") == source["expected_response"]
    assert source["independent_source"]["total_count"] == 6
    assert len(source["independent_source"]["title_matched_items"]) == 2


@pytest.mark.parametrize(
    ("response", "expected_score"),
    [
        ({"task_type": "RETRIEVE", "status": "NOT_FOUND_ERROR", "retrieved_data": None}, 1.0),
        ({"task_type": "RETRIEVE", "status": "SUCCESS", "retrieved_data": ["Invented"]}, 0.0),
    ],
    ids=["tr-225-complete-two-products-no-low-reviews", "tr-225-reject-new-low-review"],
)
def test_225_revised_reference_scores_both_directions(
    tmp_path, benchmark, catalog, response, expected_score
) -> None:
    trace = tmp_path / "task-225.har"
    trace.write_text(
        json.dumps(
            {
                "log": {
                    "version": "1.2",
                    "creator": {"name": "control", "version": "1"},
                    "entries": [
                        {
                            "request": {
                                "method": "GET",
                                "url": "http://example.test/",
                                "headers": [],
                            },
                            "response": {
                                "status": 200,
                                "headers": [],
                                "cookies": [],
                                "content": {"mimeType": "text/html", "text": ""},
                            },
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    result = evaluate_corrected_contract(
        benchmark,
        apply_correction(benchmark.get_task(225), catalog),
        agent_response=response,
        network_trace=trace,
    )
    assert float(result.score) == expected_score


def test_339_uses_upstream_issues_route_contract(benchmark, catalog) -> None:
    assert 339 not in catalog.records
    task = benchmark.get_task(339)
    assert task.revision == 3
    assert task.eval[1].expected.url.endswith("/-/issues/?$")


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


def test_475_upstream_accepts_unchecked_checkbox_forms(benchmark, catalog) -> None:
    assert 475 not in catalog.records
    task = benchmark.get_task(475)
    assert task.revision == 3
    assert task.eval[1].expected.post_data["project[initialize_with_readme]"] == (None, "0")


def test_448_checks_latest_profile_save_before_expected_value(benchmark, catalog) -> None:
    applied = apply_correction(benchmark.get_task(448), catalog)
    evaluator = _dump_eval(applied, 1)
    assert evaluator["expected"]["url"] == "__GITLAB__/-/profile"
    assert evaluator["expected"]["post_data"] == {"user[website_url]": "https://egg.tart.com"}
    assert evaluator["last_event_only"] is True


@pytest.mark.parametrize("values,expected", [
    (["https%3A%2F%2Fegg.tart.com"], (1, 1, 1)),
    (["https%3A%2F%2Fegg.tart.com", "https%3A%2F%2Fother.test"], (0, 1, 0)),
    (["https%3A%2F%2Fother.test", "https%3A%2F%2Fegg.tart.com"], (1, 1, 1)),
    (["https%3A%2F%2Fother.test"], (0, 0, 0)),
], ids=["single-correct-save", "tr-448-reject-correct-then-wrong",
        "tr-448-accept-wrong-then-correct", "single-wrong-save"])
def test_448_official_evaluator_rejects_overwritten_correct_save(
    tmp_path, benchmark, catalog, values, expected
) -> None:
    corrected = apply_correction(benchmark.get_task(448), catalog)
    predecessor = apply_correction(benchmark.get_task(448), load_corrections(
        Path("evaluation/trajectory-replay/webarena-verified/revisions/task-corrections-20260918.json")))
    target = "http://host.docker.internal:8023/-/profile"

    def entry(value: str) -> dict:
        return {
            "request": {
                "method": "POST",
                "url": target,
                "headers": [],
                "postData": {
                    "mimeType": "application/x-www-form-urlencoded",
                    "text": f"user%5Bwebsite_url%5D={value}",
                },
            },
            "response": {
                "status": 302,
                "redirectURL": target,
                "headers": [],
                "cookies": [],
                "content": {"mimeType": "text/html", "text": ""},
            },
        }

    response = {"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None}

    def score(values: list[str]) -> tuple[float, float, float]:
        har = tmp_path / "task-448.har"
        har.write_text(
            json.dumps(
                {
                    "log": {
                        "version": "1.2",
                        "creator": {"name": "task-448-control", "version": "1"},
                        "entries": [entry(value) for value in values],
                    }
                }
            ),
            encoding="utf-8",
        )
        original = benchmark.evaluate_task(
            task_id=448, agent_response=response, network_trace=har
        ).score
        fixed = evaluate_corrected_contract(
            benchmark, corrected, agent_response=response, network_trace=har
        ).score
        historical = evaluate_corrected_contract(
            benchmark, predecessor, agent_response=response, network_trace=har
        ).score
        return original, historical, fixed

    assert score(values) == expected


@pytest.mark.parametrize(
    ("website", "status", "message", "expected_score"),
    [
        ("https%3A%2F%2Fegg.tart.com", 302, "", 1),
        ("https%3A%2F%2Fegg.tart.com", 200, "Profile was successfully updated", 1),
        ("https%3A%2F%2Fegg.tart.com", 200, "Validation failed", 0),
        ("https%3A%2F%2Fother.test", 200, "Profile was successfully updated", 0),
        ("https%3A%2F%2Fegg.tart.com", 0, "Profile was successfully updated", 0),
    ],
    ids=[
        "tr-448-302-success",
        "tr-448-200-json-success",
        "tr-448-200-json-failure",
        "tr-448-wrong-website",
        "tr-448-canceled-request",
    ],
)
def test_448_accepts_only_explicit_successful_json_profile_update(
    tmp_path, benchmark, catalog, website, status, message, expected_score
) -> None:
    corrected = apply_correction(benchmark.get_task(448), catalog)
    assert len(corrected.network_alternatives[1]) == 1
    target = "http://host.docker.internal:8023/-/profile"
    response = {"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None}

    def score(*, website: str, status: int, message: str) -> float:
        har = tmp_path / "task-448-json.har"
        har.write_text(
            json.dumps(
                {
                    "log": {
                        "version": "1.2",
                        "creator": {"name": "task-448-json-control", "version": "1"},
                        "entries": [
                            {
                                "request": {
                                    "method": "POST",
                                    "url": target,
                                    "headers": [],
                                    "postData": {
                                        "mimeType": "application/x-www-form-urlencoded",
                                        "text": f"user%5Bwebsite_url%5D={website}",
                                    },
                                },
                                "response": {
                                    "status": status,
                                    "headers": [],
                                    "cookies": [],
                                    "content": {
                                        "mimeType": "application/json",
                                        "text": json.dumps({"message": message}),
                                    },
                                },
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        return evaluate_corrected_contract(
            benchmark, corrected, agent_response=response, network_trace=har
        ).score

    assert score(website=website, status=status, message=message) == expected_score


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


def test_676_uses_upstream_filter_xhr_contract(benchmark, catalog) -> None:
    assert 676 not in catalog.records
    task = benchmark.get_task(676)
    assert task.revision == 3
    assert task.eval[1].expected.query_params["filters[status]"] == ("fraud",)
    assert task.eval[1].navigation_only is False


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
