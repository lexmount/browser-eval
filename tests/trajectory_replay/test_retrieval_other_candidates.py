"""Candidate-only controls for source-derived retrieval, not outcome claims."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.retrieval_acceptance import (
    build_actual_response,
    digest,
    validate_contract,
)


ROOT = (
    Path(__file__).resolve().parents[2]
    / "evaluation/trajectory-replay/webarena-verified/retrieval-candidates"
)
SEEDS = {
    row["task_id"]: row
    for row in json.loads((ROOT / "recompiled-seed-bindings.json").read_text())["rows"]
}


@pytest.mark.parametrize("task_id", [28, 124, 132, 168, 225, 279, 303, 386])
def test_current_read_observation_binds_exact_canonical_prefix_and_action(task_id: int):
    candidate = json.loads((ROOT / f"task-{task_id}.json").read_text())
    calls, contract = candidate["tool_calls"], candidate["retrieval_contract"]
    seed = SEEDS[task_id]

    assert candidate["task_id"] == contract["task_id"] == task_id
    assert len(calls) == seed["canonical_action_count"] + 1
    assert digest(calls[: seed["canonical_action_count"]]) == seed["canonical_calls_sha256"]
    assert contract["sources"] == [
        {
            "name": {168: "owned", 225: "catalog"}.get(task_id, "observation"),
            "step": len(calls),
            "action_sha256": digest(calls[-1]),
            "decode": "json",
        }
    ]
    validate_contract(contract, calls)

    changed = copy.deepcopy(calls)
    changed[-1]["script"] += "\n// changed observation"
    with pytest.raises(ValueError, match="canonical actions"):
        validate_contract(contract, changed)


@pytest.mark.parametrize("task_id", [28, 124, 132, 168, 225, 279, 303, 386])
def test_candidate_has_no_frozen_answer_or_mutating_network_call(task_id: int):
    candidate = json.loads((ROOT / f"task-{task_id}.json").read_text())
    script = candidate["tool_calls"][-1]["script"]
    assert "fetch(" in script
    assert "redirect:'error'" in script
    assert "POST" not in script
    assert "PUT" not in script
    assert "DELETE" not in script
    assert "PATCH" not in script
    assert "answer:" in script or "answer};" in script
    assert candidate["retrieval_contract"]["projection"] != {"source": "reference", "pointer": "/answer"}


@pytest.mark.parametrize("task_id", [124, 279, 386])
def test_shopping_candidates_require_declared_total_and_linked_products(task_id: int):
    script = json.loads((ROOT / f"task-{task_id}.json").read_text())["tool_calls"][-1]["script"]
    assert "rows.length!==total" in script
    assert "total!==data.total_count" in script
    assert "data.page_info.current_page!==page" in script
    assert "location.origin" in script
    assert "seen.has(row.sku)" in script


def test_task168_absence_requires_complete_authenticated_personal_project_set():
    candidate = json.loads((ROOT / "task-168.json").read_text())
    calls, contract = candidate["tool_calls"], candidate["retrieval_contract"]
    assert contract["schema"].endswith("/3")
    assert contract["absence_coverage"]["kind"] == "exhaustive_records/1"
    assert contract["absence_coverage"]["predicate"] == {
        "field_pointer": "/star_count",
        "type": "integer",
        "operator": "gt",
        "value": 100,
    }
    rows = [
        {
            "id": 1,
            "star_count": 6,
            "namespace_kind": "user",
            "owner_id": 2330,
            "path_with_namespace": "byteblaze/a",
            "visibility": "private",
        },
        {
            "id": 2,
            "star_count": 0,
            "namespace_kind": "user",
            "owner_id": 2330,
            "path_with_namespace": "byteblaze/b",
            "visibility": "public",
        },
    ]
    observation = {
        "scope": {"owner_username": "byteblaze", "namespace_kind": "user"},
        "scope_ok": True,
        "complete": True,
        "page_size": 100,
        "total": 2,
        "pages": [{"page": 1, "rows": 2, "total": 2, "has_next": False, "ids": ["1", "2"]}],
        "records": rows,
        "match_count": 0,
        "answer": [],
    }

    def evaluate(value: dict) -> dict:
        records = [{} for _ in calls]
        records[-1] = {
            "index": len(calls),
            "arguments": calls[-1],
            "status": "success",
            "tool_status": "success",
            "result": json.dumps(value),
        }
        return build_actual_response(
            contract=contract,
            task_id=168,
            calls=calls,
            records=records,
            run_id="synthetic-run",
            attempt_id="synthetic-attempt",
        )

    green = evaluate(observation)
    assert green["status"] == "supported"
    assert green["actual_response"]["status"] == "NOT_FOUND_ERROR"
    for change in (
        lambda x: x["records"][0].update(star_count=101),
        lambda x: x["pages"][0].update(rows=1),
        lambda x: x["pages"][0].update(ids=["1", "1"]),
        lambda x: x["scope"].update(owner_username="another-user"),
    ):
        mutated = copy.deepcopy(observation)
        change(mutated)
        assert evaluate(mutated)["status"] == "insufficient"


def test_task225_absence_requires_complete_sephora_brush_product_reviews():
    candidate = json.loads((ROOT / "task-225.json").read_text())
    calls, contract = candidate["tool_calls"], candidate["retrieval_contract"]
    script = calls[-1]["script"]
    assert "source.total_count" in script
    assert "all.length!==source.total_count" in script
    assert "review_count" in script
    assert "info?.total_pages" in script
    assert "items.length!==product.review_count" in script
    assert "manufacturer" not in script
    rows = [
        {
            "id": "SKU-A",
            "name": "Sephora Brush Set",
            "review_count": 0,
            "low_rating_count": 0,
        },
        {
            "id": "SKU-B",
            "name": "Sephora Powder Brush",
            "review_count": 0,
            "low_rating_count": 0,
        },
    ]
    observation = {
        "scope": {"catalog_filter": "name match Sephora", "product_selection": "title contains Brush"},
        "scope_ok": True,
        "complete": True,
        "source_total": 6,
        "records": rows,
        "pages": [{"page": 1, "rows": 2, "total": 2, "has_next": False, "ids": ["SKU-A", "SKU-B"]}],
        "match_count": 0,
        "answer": [],
    }

    def evaluate(value: dict) -> dict:
        records = [{} for _ in calls]
        records[-1] = {
            "index": len(calls),
            "arguments": calls[-1],
            "status": "success",
            "tool_status": "success",
            "result": json.dumps(value),
        }
        return build_actual_response(
            contract=contract,
            task_id=225,
            calls=calls,
            records=records,
            run_id="synthetic-run",
            attempt_id="synthetic-attempt",
        )

    green = evaluate(observation)
    assert green["status"] == "supported"
    assert green["actual_response"]["status"] == "NOT_FOUND_ERROR"
    for change in (
        lambda x: x["records"][0].update(low_rating_count=1),
        lambda x: x["pages"][0].update(rows=1),
        lambda x: x["pages"][0].update(ids=["SKU-A", "SKU-A"]),
        lambda x: x["scope"].update(product_selection="all products"),
    ):
        mutated = copy.deepcopy(observation)
        change(mutated)
        assert evaluate(mutated)["status"] == "insufficient"


def test_task225_revised_reference_receipt_binds_independent_scope_and_candidate():
    path = ROOT / "task-225.json"
    candidate = json.loads(path.read_text())
    receipt = json.loads(
        (ROOT.parents[3] / "research/trajectory-replay-task225-revised-reference.json").read_text()
    )
    source = receipt["independent_source"]
    products = source["title_matched_items"]
    binding = receipt["candidate_binding"]

    assert receipt["task_id"] == candidate["task_id"] == 225
    assert binding["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert binding["contract_schema"] == candidate["retrieval_contract"]["schema"]
    assert binding["absence_coverage"] == candidate["retrieval_contract"]["absence_coverage"]["kind"]
    assert source["total_count"] == source["returned_items"] == 6
    assert source["page_size"] >= source["total_count"]
    assert len(products) == 2
    assert len({row["sku"] for row in products}) == len(products)
    assert all("sephora" in row["name"].lower() and "brush" in row["name"].lower() for row in products)
    assert all(row["review_count"] == len(row["reviews"]["items"]) == 0 for row in products)
    assert all(row["reviews"]["page_info"] == {"current_page": 1, "total_pages": 0, "page_size": 100} for row in products)
    assert receipt["database_cross_check"]["catalog_match_count"] == len(products)
    assert receipt["database_cross_check"]["review_row_count"] == 0
    assert receipt["database_cross_check"]["manufacturer_attribute_rows"] == 0
    assert receipt["expected_response"]["status"] == candidate["retrieval_contract"]["empty_result"] == "NOT_FOUND_ERROR"
    assert candidate["retrieval_contract"]["absence_coverage"]["expected_scope"] == {
        key: receipt["selection_rule"][key] for key in ("catalog_filter", "product_selection")
    }
