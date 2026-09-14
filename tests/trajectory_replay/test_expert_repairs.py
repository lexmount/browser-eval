from __future__ import annotations

import json
from pathlib import Path

REPAIRS = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "trajectory-replay"
    / "webarena-verified"
    / "expert-repairs.json"
)
TASK_IDS = {
    28,
    124,
    183,
    184,
    198,
    225,
    258,
    279,
    284,
    320,
    334,
    339,
    415,
    464,
    470,
    475,
    506,
    510,
    528,
    547,
    576,
    585,
    600,
    605,
    676,
    694,
    699,
    725,
}


def _payload() -> dict:
    return json.loads(REPAIRS.read_text(encoding="utf-8"))


def _contains_ref(value: object) -> bool:
    if isinstance(value, dict):
        return "ref" in value or any(_contains_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_ref(item) for item in value)
    return False


def test_expert_repairs_have_exact_scope_and_stable_calls() -> None:
    payload = _payload()
    assert payload["schema"] == "lex_webarena_expert_seed_repairs/1"
    assert {item["task_id"] for item in payload["repairs"]} == TASK_IDS
    assert len(payload["repairs"]) == len(TASK_IDS)
    assert not _contains_ref(payload)
    for repair in payload["repairs"]:
        assert repair["source"]["task_revision"] == 2
        assert repair["repair_basis"]["root_cause"]
        assert repair["repair_basis"]["strategy"]
        assert repair["tool_calls"]
        for call in repair["tool_calls"]:
            if call["action"] == "wait":
                assert set(call) == {"action", "seconds"}
                assert call["seconds"] > 0


def test_mutation_repairs_preserve_task_specific_semantics() -> None:
    repairs = {item["task_id"]: item for item in _payload()["repairs"]}
    calls = {
        key: json.dumps(value["tool_calls"], ensure_ascii=False)
        for key, value in repairs.items()
    }

    assert "catalog/product/edit/id/1796" in calls[464]
    assert "product[short_description]" in calls[464]
    assert "sales/order/view/order_id/302" in calls[470]
    assert "projects/new#blank_project" in calls[475]
    assert "sales/order/" in calls[676]

    assert "WorcesterMA/new" in calls[28]
    assert "/f/Worcester\"" not in calls[28]
    assert "Clear all" not in calls[184]
    assert "a.product-item-link" in calls[284]
    assert "order_id/156" in calls[320] and "order_id/158" in calls[320]
    assert "order_id/180" in calls[334]
    assert "merge_requests/40" in calls[415]
    assert "@davepgreene" in calls[415]
    assert "nth-match" not in calls[506]
    assert "abisubramanya27" in calls[576] and "lahwaacz" in calls[576]
    assert 'ratings[4]\\\"][value=\\\"20' in calls[585]
    assert "/submit/consoles" in calls[600]
    assert "/submit/consoles" in calls[605]
    assert "RickyDontLoseThat/submissions" in calls[725]
    assert "img[alt=\\\"down\\\"]" in calls[725]

    assert len(repairs[528]["tool_calls"]) == 2
    assert "Emma Lopez" in calls[528]
    assert "000000180" in calls[528]
    assert "submit" not in calls[528].lower()

    assert "product 75640" in repairs[510]["repair_basis"]["root_cause"]
    assert "expected one cart item" in calls[510]

    assert "S/Brown" in repairs[547]["repair_basis"]["strategy"]
    for marker in (
        "box.labels?.[0]",
        "label==='S'||label==='Brown'",
        "#apply-single-price-radio",
        "#apply-single-inventory-radio",
        "Generate Products",
        "#save-button",
    ):
        assert marker in calls[547]

    for value in ("Energy-Bulk Women Shirt", "'50'", "'167'"):
        assert value in calls[694]

    assert "/sales_rule/promo_quote/new/" in calls[699]
    assert "/catalog_rule/" not in calls[699]
    assert "['1','2','3']" in calls[699]
