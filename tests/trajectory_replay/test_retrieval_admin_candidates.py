"""Static safety gates for current-source Magento retrieval candidates.

These gates do not qualify a live observer or replace the full three-method cohort.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.trajectory_replay.retrieval_acceptance import validate_contract

ROOT = Path("evaluation/trajectory-replay/webarena-verified/retrieval-candidates")
TASK_IDS = (41, 50, 77, 94, 110, 117, 127, 183, 184, 188, 193, 198, 231, 288, 320, 334)


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_candidate_binds_current_read_to_exact_browsertool_calls(task_id: int) -> None:
    candidate = json.loads((ROOT / f"task-{task_id}.json").read_text())
    calls = candidate["tool_calls"]
    assert candidate["task_id"] == task_id
    assert [call["action"] for call in calls] == ["navigate", "evaluate"]
    validate_contract(candidate["retrieval_contract"], calls)
    source = candidate["retrieval_contract"]["sources"]
    assert len(source) == 1 and source[0]["step"] == 2
    assert candidate["retrieval_contract"]["requirements"] == {
        "scope": {"source": "current", "pointer": "/scope"},
        "completeness": {"source": "current", "pointer": "/complete"},
    }
    projection = {"source": "current", "pointer": "/answer"}
    if task_id == 183:
        projection = {"distinct_exact_strings": projection}
    assert candidate["retrieval_contract"]["projection"] == projection
    script = calls[1]["script"]
    assert "fetch(" in script and "same-origin" in script
    assert "redirect:'error'" in script
    assert "scope:true,complete:true" in script
    assert "throw Error(" in script or "throw new Error(" in script
    assert "location.assign(" not in script
    assert ".click(" not in script
    assert ".submit(" not in script


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_candidate_rejects_a_modified_observation_action(task_id: int) -> None:
    candidate = json.loads((ROOT / f"task-{task_id}.json").read_text())
    candidate["tool_calls"][-1]["script"] += "\n// unbound change"
    with pytest.raises(ValueError, match="canonical actions"):
        validate_contract(candidate["retrieval_contract"], candidate["tool_calls"])


@pytest.mark.parametrize("task_id", (41, 77, 94, 110, 127, 183, 184, 193, 198, 288))
def test_admin_grid_candidates_require_exhaustive_pages(task_id: int) -> None:
    script = json.loads((ROOT / f"task-{task_id}.json").read_text())["tool_calls"][-1]["script"]
    if task_id in (41, 77, 127):
        assert "gridId+'-total-count'" in script
        assert "data.total!==total||data.size!==first.size" in script
        assert "new Set(rows.map(r=>r[identity])).size!==total" in script
    else:
        assert "totalRecords" in script
    assert "pages" in script
    assert "Math.ceil(total/size)>100" in script
    assert "rows.length!==total" in script
    assert "incomplete" in script


@pytest.mark.parametrize("task_id", (50, 117, 188, 231, 320, 334))
def test_storefront_history_candidates_require_exhaustive_pages(task_id: int) -> None:
    script = json.loads((ROOT / f"task-{task_id}.json").read_text())["tool_calls"][-1]["script"]
    assert ".toolbar-amount" in script
    assert "history pagination bounds inconsistent" in script
    assert "data.total!==total||data.from!==rows.length+1" in script
    assert "Math.ceil(total/pageSize)" in script
    assert "rows.length!==total" in script
    assert "duplicate or incomplete history" in script
    assert "/sales/order/history/" in script


def test_customer_email_ranking_uses_all_canceled_order_rows() -> None:
    script = json.loads((ROOT / "task-288.json").read_text())["tool_calls"][-1]["script"]
    assert "rows.filter(r=>r.status==='canceled')" in script
    assert "row.customer_email" in script
    assert "most cancellations are tied" in script
    assert "Samantha Jones" not in script
    assert "order_id/88" not in script


def test_stock_candidates_do_not_select_recorded_product_ids() -> None:
    for task_id in (183, 184):
        script = json.loads((ROOT / f"task-{task_id}.json").read_text())[
            "tool_calls"
        ][-1]["script"]
        assert "Number(qty)" in script
        assert "selected.map" in script
        assert "order_id/" not in script
