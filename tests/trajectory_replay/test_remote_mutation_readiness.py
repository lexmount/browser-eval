"""Offline controls for bounded pre-action readiness and source-state gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay import remote_execution as runner
from harness.trajectory_replay.webarena_plan import (
    compile_mutation_observer,
    compile_mutation_readiness,
    verify_compiled_mutation_proposal,
)

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = (
    ROOT / "evaluation/trajectory-replay/webarena-verified/mutation-completion-candidates.json"
)


def test_task465_gate_is_bound_to_fifth_original_action_and_resolved_deferred():
    contract, script = compile_mutation_readiness(CANDIDATES, 465, 8)
    assert contract == {
        "kind": "amd_deferred_ready",
        "before_original_action": 5,
        "module": "Magento_Customer/js/customer-data",
        "deferred_method": "getInitCustomerData",
        "timeout_ms": 15000,
    }
    assert 'require([moduleName], resolve, reject)' in script
    assert "deferred.done(resolve).fail(reject)" in script
    assert "after.state !== 'resolved'" in script
    assert "const timeoutMs = 15000;" in script
    assert not any(token in script for token in ("fetch(", "XMLHttpRequest", "submit(", "click("))


def test_old_candidate_without_gate_remains_compatible():
    assert compile_mutation_readiness(CANDIDATES, 585, 11) == (None, None)


def test_task464_requires_frozen_product_type_before_the_first_action():
    contract, script = compile_mutation_readiness(CANDIDATES, 464, 7)
    assert contract == {
        "kind": "same_origin_magento_product_type",
        "before_original_action": 1,
        "path": "/admin/catalog/product/edit/id/1796/",
        "product_id": 1796,
        "expected_type": "configurable",
        "timeout_ms": 15000,
    }
    assert "credentials:'same-origin'" in script
    assert "redirect:'error'" in script
    assert "actualType !== expectedType" in script
    assert "product_data_source_not_unique" in script
    assert not any(token in script for token in ("submit(", "click(", "method:'POST'"))


@pytest.mark.parametrize(
    "override",
    [
        {"before_original_action": 2},
        {"product_id": 1797},
        {"product_id": True},
        {"path": "//other-host/admin/catalog/product/edit/id/1796/"},
        {"expected_type": "simple; fetch('/write')"},
        {"timeout_ms": 0},
        {"script": "fetch('/write')"},
    ],
)
def test_task464_source_state_gate_rejects_invalid_contract(tmp_path, override):
    payload = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    row = next(row for row in payload["rules"] if row["task_id"] == 464)
    row["pre_action_readiness"].update(override)
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        compile_mutation_readiness(path, 464, 7)


@pytest.mark.parametrize(
    "override",
    [
        {"before_original_action": 9},
        {"before_original_action": True},
        {"timeout_ms": 0},
        {"timeout_ms": 30001},
        {"module": "x'); fetch('/write') //"},
        {"deferred_method": "constructor()"},
        {"script": "fetch('/write')"},
    ],
)
def test_gate_rejects_out_of_bounds_or_arbitrary_scripts(tmp_path, override):
    payload = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    row = next(row for row in payload["rules"] if row["task_id"] == 465)
    row["pre_action_readiness"].update(override)
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        compile_mutation_readiness(path, 465, 8)


def test_gate_rejects_wrong_canonical_action_count():
    with pytest.raises(ValueError, match="canonical action count"):
        compile_mutation_readiness(CANDIDATES, 465, 7)


def test_compiled_wishlist_observer_rejects_stale_relative_href_proposal():
    rule, script = compile_mutation_observer(CANDIDATES, 465, 8)
    assert rule["observer"]["href_origin"] == "response_url_origin"
    assert "new URL(href, base)" in script
    assert "url.origin === origin" in script
    assert "credentials: 'same-origin'" in script
    assert "method: 'GET'" in script
    proposal = {
        "tool_calls": [{"action": "evaluate", "script": script}],
        "expected_completion": "persisted_wishlist_item_observed",
        "observer_contract_sha256": hashlib.sha256(script.encode()).hexdigest(),
        "completion_candidates_sha256": runner.file_sha256(CANDIDATES),
    }
    verify_compiled_mutation_proposal(proposal, rule, script, CANDIDATES)
    proposal["tool_calls"][-1]["script"] = (
        '() => document.querySelector(\'a[href="/customer/account/logout/"]\')'
    )
    with pytest.raises(ValueError, match="stale"):
        verify_compiled_mutation_proposal(proposal, rule, script, CANDIDATES)


@pytest.mark.parametrize(
    ("status", "result", "proven"),
    [
        ("success", '{"ready":true,"module":"Magento_Customer/js/customer-data",'
         '"method":"getInitCustomerData","after":{"state":"resolved"}}', True),
        ("success", '{"ready":true,"module":"Magento_Customer/js/customer-data",'
         '"method":"getInitCustomerData","after":{"state":"pending"}}', False),
        ("error", "Evaluate timed out", False),
        ("success", "not JSON", False),
    ],
)
def test_guard_execution_records_red_and_green_before_original_click(
    monkeypatch, status, result, proven
):
    contract, script = compile_mutation_readiness(CANDIDATES, 465, 8)
    calls = []

    def execute(_tool, arguments):
        calls.append(arguments)
        return status, result

    monkeypatch.setattr(runner, "_execute_replay_call", execute)
    row, observed = runner.execute_mutation_readiness(object(), contract, script)
    assert calls == [{"action": "evaluate", "script": script}]
    assert row["index"] == "before-5"
    assert row["kind"] == "pre_action_readiness"
    assert ("validation_error" not in row) is proven
    assert (observed is not None and observed["after"]["state"] == "resolved") is proven


@pytest.mark.parametrize(
    ("product_type", "proven"),
    [("configurable", True), ("simple", False)],
)
def test_product_source_state_gate_rejects_drift_before_save(monkeypatch, product_type, proven):
    contract, script = compile_mutation_readiness(CANDIDATES, 464, 7)
    observed = {
        "ready": True, "productId": 1796, "productType": product_type,
        "sourceCount": 1,
    }
    monkeypatch.setattr(
        runner, "_execute_replay_call", lambda _tool, _args: ("success", json.dumps(observed))
    )
    row, result = runner.execute_mutation_readiness(object(), contract, script)
    assert row["index"] == "before-1"
    assert ("validation_error" not in row) is proven
    assert result == observed
