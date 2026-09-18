import hashlib
import json
from pathlib import Path

import pytest

import harness.trajectory_replay.diagnostic_closure as closure_module
from harness.trajectory_replay.diagnostic_closure import (
    CURRENT_FINDINGS,
    CURRENT_TASKS,
    LATEST_BINARY_SHA256,
    LATEST_SOURCE_HEAD,
    LATEST_TASKS,
    _load_latest_index,
    _qualified_trace_count,
    _validate_forum_link_geometry_diagnostic,
    _validate_gitlab_avatar_geometry_diagnostic,
    _validate_sort_menu_geometry_diagnostic,
    _validate_task66_geometry_diagnostic,
    _validate_task163_old_action_counterexample,
    _validate_task163_review_source,
    _validate_task163_visibility_probes,
    _validate_task196_204_old_actions,
    _validate_task196_204_orders_source,
    _validate_task243_identity_audit,
    _validate_task243_source_mechanism_audit,
    _validate_task313_absence_audit,
    _validate_task313_catalog_counterexample,
    _validate_task313_store_support_audit,
    _verified_digest,
    load_diagnostic_closure,
)
from harness.trajectory_replay.webarena_report import (
    _assets,
    _latest_moli_task_section,
    _write_diagnostic_closure,
)


def test_bounded_unresolved_audit_does_not_require_audit_for_all_unresolved_tasks() -> None:
    assert not closure_module._audit_category_requires_binding("unresolved")
    assert not closure_module._audit_category_requires_binding(None)
    assert closure_module._audit_category_requires_binding("stale_layout_snapshot_after_navigation")


def test_retrospective_retrieval_findings_do_not_claim_current_qualification() -> None:
    chinese, english = CURRENT_FINDINGS[243][2:]
    assert "当前候选" in chinese
    assert "current candidate" in english
    assert "qualified output" not in english.lower()
    chinese, english = CURRENT_FINDINGS[313][2:]
    assert "有限范围" in chinese
    assert "bounded scope" in english


def test_task243_guest_review_cannot_be_promoted_as_account_identity() -> None:
    raw = {
        "schema": "task243_remote_read_only_identity_audit/1",
        "task_id": 243,
        "identity_verified": False,
        "review_rows": [{"review_id": 353, "customer_id": None, "nickname": "Hannah Lim"}],
        "exact_name_customer_rows": [{"firstname": "Hannah", "lastname": "Lim"}],
        "associated_order_aggregate": {"matching_orders": 0},
    }
    public = {
        "schema": "lex_replay_task_identity_source_audit/1",
        "task_id": 243,
        "source_audit_sha256": "a" * 64,
        "review_id": 353,
        "review_customer_id": None,
        "exact_name_account_count": 1,
        "matching_order_count": 0,
        "verified_author_account_link": False,
        "rating_vote_audit": {
            "receipt_sha256": "b" * 64,
            "review_id": 353,
            "vote_customer_id": None,
            "review_link_tables_checked": ["rating_option_vote", "review_detail"],
        },
    }
    vote = {
        "schema": "task243_remote_read_only_rating_vote_audit/1",
        "task_id": 243,
        "identity_verified": False,
        "tables_with_both_review_id_and_customer_id": ["rating_option_vote", "review_detail"],
        "rating_option_vote_rows": [{"review_id": 353, "customer_id": None}],
    }
    _validate_task243_identity_audit("a" * 64, raw, public, "b" * 64, vote)
    raw["review_rows"][0]["customer_id"] = 66
    with pytest.raises(ValueError, match="guest identity"):
        _validate_task243_identity_audit("a" * 64, raw, public, "b" * 64, vote)
    raw["review_rows"][0]["customer_id"] = None
    public["verified_author_account_link"] = True
    with pytest.raises(ValueError, match="guest identity"):
        _validate_task243_identity_audit("a" * 64, raw, public, "b" * 64, vote)
    public["verified_author_account_link"] = False
    vote["rating_option_vote_rows"][0]["customer_id"] = 66
    with pytest.raises(ValueError, match="guest identity"):
        _validate_task243_identity_audit("a" * 64, raw, public, "b" * 64, vote)


def test_task243_guest_review_source_mechanism_rejects_fabricated_identity() -> None:
    path = Path(__file__).resolve().parents[2] / (
        "research/trajectory-replay-task243-source-mechanism-audit.json"
    )
    audit = json.loads(path.read_text(encoding="utf-8"))
    _validate_task243_source_mechanism_audit(audit)
    audit["database_linkage_inventory"]["review_detail_customer_id"] = 66
    with pytest.raises(ValueError, match="guest review source mechanism"):
        _validate_task243_source_mechanism_audit(audit)
    audit["database_linkage_inventory"]["review_detail_customer_id"] = None
    audit["post_hoc_reference_validity"]["diagnostic_answer_eligible"] = True
    with pytest.raises(ValueError, match="guest review source mechanism"):
        _validate_task243_source_mechanism_audit(audit)
    audit["post_hoc_reference_validity"]["diagnostic_answer_eligible"] = False
    audit["independent_task_curation"]["source_title"] = "WebArena-Verified"
    with pytest.raises(ValueError, match="guest review source mechanism"):
        _validate_task243_source_mechanism_audit(audit)
    audit["independent_task_curation"]["source_title"] = (
        "WONDERBREAD: A Benchmark for Evaluating Multimodal Foundation Models on Business Process Management Tasks"
    )
    audit["upstream_dataset_comparison"]["raw_reference_url"] = "invented-source"
    with pytest.raises(ValueError, match="guest review source mechanism"):
        _validate_task243_source_mechanism_audit(audit)


def test_task313_verified_not_found_stays_within_store_support_scope() -> None:
    path = Path(__file__).resolve().parents[2] / (
        "research/trajectory-replay-task313-absence-audit.json"
    )
    audit = json.loads(path.read_text(encoding="utf-8"))
    _validate_task313_absence_audit(
        audit,
        audit["independent_support_source_audit_sha256"],
        audit["third_party_product_phone_counterexample_sha256"],
    )
    audit["scope_limit_en"] = "No phone exists anywhere on the site."
    with pytest.raises(ValueError, match="task-scoped absence"):
        _validate_task313_absence_audit(
            audit,
            audit["independent_support_source_audit_sha256"],
            audit["third_party_product_phone_counterexample_sha256"],
        )


def test_task313_source_audit_is_scoped_and_complete() -> None:
    origin = "http://shopping.example"
    receipt = {
        "schema": "task313_store_support_source_audit/1",
        "observed_at_utc": "2026-09-17T16:55:22+00:00",
        "store_phone_config": None,
        "cms_pages": [
            {"id": identifier, "identifier": name, "content_sha256": "a" * 64,
             "phone_number_matches": 0}
            for identifier, name in (
                (1, "no-route"), (2, "home"), (3, "enable-cookies"),
                (4, "privacy-policy-cookie-restriction-mode"),
            )
        ],
        "cms_block_count": 0,
        "contact": {
            "url": origin + "/contact/", "http_status": 200,
            "html_sha256": "b" * 64, "visible_text_sha256": "c" * 64,
            "visible_chars": 1466, "tel_link_count": 0, "phone_number_matches": 0,
        },
        "scope": "Store support pages; does not cover vendor product descriptions",
    }
    _validate_task313_store_support_audit(receipt, origin)
    receipt["contact"]["phone_number_matches"] = 1
    with pytest.raises(ValueError, match="task 313 store-support"):
        _validate_task313_store_support_audit(receipt, origin)
    receipt["contact"]["phone_number_matches"] = 0
    receipt["cms_pages"].pop()
    with pytest.raises(ValueError, match="task 313 store-support"):
        _validate_task313_store_support_audit(receipt, origin)


def test_task313_catalog_phone_cannot_be_treated_as_store_phone() -> None:
    receipt = {
        "schema": "task313_catalog_third_party_phone_counterexample/1",
        "index": "magento2_product_1_v4",
        "index_document_count": 104368,
        "document_id": "97396",
        "description_sha256": "a" * 64,
        "quoted_context": "Amazon Customer Service: 1 (888)280-4331",
        "interpretation": "This is not the One Stop Market store phone",
    }
    _validate_task313_catalog_counterexample(receipt)
    receipt["quoted_context"] = "One Stop Market: 1 (888)280-4331"
    with pytest.raises(ValueError, match="task 313 catalog counterexample"):
        _validate_task313_catalog_counterexample(receipt)


def test_task163_source_requires_all_reviews_and_distinct_low_rating_titles() -> None:
    rows = [
        {
            "review_id": 283443 + index,
            "status_id": 1,
            "entity_pk_value": 38805,
            "title": "Memory Card Came Defective" if index in (0, 3) else f"Other {index}",
            "vote_count": 1,
            "stars": 1 if index in (0, 3) else 4,
            "percent": 20 if index in (0, 3) else 80,
        }
        for index in range(12)
    ]
    source = {
        "schema": "task163_review_source_audit/1",
        "observed_at_utc": "2026-09-18T08:00:00+00:00",
        "source": {"app_id": 195, "store_id": 1, "product_id": 38805},
        "review_count": 12,
        "low_rating_review_count": 2,
        "distinct_low_rating_titles": ["Memory Card Came Defective"],
        "rows": rows,
    }
    actual = {
        "task_type": "RETRIEVE",
        "status": "SUCCESS",
        "retrieved_data": ["Memory Card Came Defective"],
    }
    _validate_task163_review_source(source, actual)
    actual["retrieved_data"] = ["Memory Card Came Defective", "Other 1"]
    with pytest.raises(ValueError, match="task 163 answer"):
        _validate_task163_review_source(source, actual)
    actual["retrieved_data"] = ["Memory Card Came Defective"]
    rows[3]["title"] = "Another low-rating title"
    with pytest.raises(ValueError, match="task 163 answer"):
        _validate_task163_review_source(source, actual)
    rows[3]["title"] = "Memory Card Came Defective"
    rows.pop()
    with pytest.raises(ValueError, match="task 163 independent review source"):
        _validate_task163_review_source(source, actual)


def test_task163_old_action_counterexample_requires_same_step_four_target() -> None:
    selector = "review/product/listAjax/id/38805/?p=2"
    fixed_trace = [
        {"index": index, "tool_status": "success", "arguments": {"action": str(index)}}
        for index in range(1, 6)
    ]
    old_trace = [dict(step) for step in fixed_trace[:4]]
    old_trace[3]["tool_status"] = "error"
    old_trace[3]["result"] = f"{selector} Timeout 60000ms exceeded"
    receipt = {
        "schema": "remote_exact_frozen_actions_diagnostic/1",
        "task_id": 163,
        "status": "diagnostic_incomplete",
        "completed_action_count": 3,
        "frozen_action_count": 5,
        "official_evaluation": "not_run",
        "site_reset": False,
        "promotable": False,
        "trace_sha256": "a" * 64,
        "input_sha256": {
            "binary": "3a967a1280ffe434e678b79ec24238701cbf708154dbdd490f8b9d78d03032ff"
        },
    }
    _validate_task163_old_action_counterexample(
        receipt, "a" * 64, old_trace, fixed_trace
    )
    old_trace[3]["arguments"] = {"action": "different"}
    with pytest.raises(ValueError, match="task 163 old-action"):
        _validate_task163_old_action_counterexample(
            receipt, "a" * 64, old_trace, fixed_trace
        )


def test_task163_visibility_probe_rejects_missing_login_phase_or_dom_drift(
    tmp_path: Path,
) -> None:
    refs = []
    target = "review/product/listAjax/id/38805/?p=2"
    for index, count in enumerate((2, 0, 0, 2)):
        visible = [False, False, True, True] if count else [False] * 4
        probe = {
            "viewport": {
                "requested": {"width": 1280, "height": 900},
                "observations": [
                    {"phase": "login_before_navigation"}
                ] if index >= 2 else [],
            },
            "steps": [{"status": "success"}] * 2,
            "observations": [
                {
                    "after_seconds": second,
                    "matches": [{"html": f'<a href="{target}">2</a>'}] * 4,
                    "playwright_visible_count": count,
                    "playwright_visible": visible,
                }
                for second in (0, 2, 7, 17)
            ],
        }
        refs.append(_write(tmp_path / f"probe-{index}.json", probe))
    _validate_task163_visibility_probes(refs)
    bad = json.loads(Path(refs[3]["path"]).read_text())
    bad["viewport"]["observations"] = []
    refs[3] = _write(tmp_path / "bad.json", bad)
    with pytest.raises(ValueError, match="task 163 visibility probe result"):
        _validate_task163_visibility_probes(refs)


def test_task196_204_order_source_checks_answer_and_parent_items() -> None:
    ids = {"canceled": (136, 242, 25, 118, 98),
           "complete": (230, 256, 9, 113, 148)}
    amounts = {"canceled": (20820, 18300, 20425, 2900, 500),
               "complete": (9340, 8900, 15940, 8840, 500)}
    source = {
        "schema": "task196_204_orders_independent_source/1",
        "observed_at_utc": "2026-09-18T08:00:00+00:00",
        "source": {
            "app_id": 195,
            "service_id": "47ef97769c9644039161acb043d911c1",
        },
        "timezone": "America/New_York",
        "database_total": 308,
    }
    for status in ("canceled", "complete"):
        source[status] = [
            {
                "entity_id": entity_id,
                "increment_id": f"{entity_id:09d}",
                "status": status,
                "created_at": f"2023-05-{20-index:02d} 12:00:00",
                "order_currency_code": "USD",
                "grand_total": f"{cents / 100:.4f}",
                "subtotal": "83.4000" if entity_id == 230 else "0.0000",
            }
            for index, (entity_id, cents) in enumerate(
                zip(ids[status], amounts[status], strict=True)
            )
        ]
    source["latest_complete_order_items"] = [
        {
            "item_id": item_id,
            "order_id": 230,
            "parent_item_id": parent,
            "name": name,
            "qty_ordered": "1.0000",
            "price": f"{price / 100:.4f}",
            "original_price": f"{original / 100:.4f}",
            "row_total": f"{price / 100:.4f}",
            "tax_amount": "0.0000",
            "discount_amount": "0.0000",
        }
        for item_id, parent, name, price, original in (
            (1235, None, "Proteus Fitness Jackshirt", 4500, 4500),
            (1236, 1235, "Proteus variant", 0, 0),
            (1237, None, "Ida Workout Parachute Pant", 3840, 4800),
            (1238, 1237, "Ida variant", 0, 0),
        )
    ]
    pages = [{"page": 1, "rows": 200, "total": 308},
             {"page": 2, "rows": 108, "total": 308}]
    selected = {
        status: [
            {
                "entity_id": str(row["entity_id"]),
                "created_at": f"2023-05-{20-index:02d} 08:00:00",
                "status": status,
                "currency": "USD",
                "cents": amounts[status][index],
            }
            for index, row in enumerate(source[status][:4])
        ]
        for status in ("canceled", "complete")
    }
    answer196 = [(sum(amounts["canceled"][:4]) - sum(amounts["complete"][:4])) / 100]
    observed196 = {
        "scope": True, "complete": True, "total": 308, "pages": pages,
        "selected": selected,
        "canceled_cents": sum(amounts["canceled"][:4]),
        "complete_cents": sum(amounts["complete"][:4]),
        "answer": answer196,
    }
    trace196 = [{"tool_status": "success", "arguments": {"action": "evaluate"},
                 "result": json.dumps(observed196)}]
    actual196 = {"task_type": "RETRIEVE", "status": "SUCCESS",
                 "retrieved_data": answer196}
    _validate_task196_204_orders_source(196, source, trace196, actual196)
    actual196["retrieved_data"] = [0]
    with pytest.raises(ValueError, match="task 196 browser order scope"):
        _validate_task196_204_orders_source(196, source, trace196, actual196)
    actual196["retrieved_data"] = answer196
    detail_items = [
        {"id": "order_item_1235", "name": "Proteus Fitness Jackshirt", "qty": 1,
         "unit": 4500, "original": 4500, "subtotal": 4500, "tax": 0,
         "discount": 0, "rowtotal": 4500},
        {"id": "order_item_1237", "name": "Ida Workout Parachute Pant", "qty": 1,
         "unit": 3840, "original": 4800, "subtotal": 3840, "tax": 0,
         "discount": 0, "rowtotal": 3840},
    ]
    answer204 = [{"name": "Ida Workout Parachute Pant", "price": 38.4},
                 {"name": "Proteus Fitness Jackshirt", "price": 45}]
    observed204 = {
        "scope": True, "complete": True, "total": 308, "pages": pages,
        "selected": {"entity_id": "230", "increment_id": "000000230",
                     "created_at": "2023-05-20 08:00:00", "status": "complete"},
        "detail": {"entity_id": "230", "increment_id": "000000230",
                   "body_count": 2, "items": detail_items},
        "answer": answer204,
    }
    trace204 = [{"tool_status": "success", "arguments": {"action": "evaluate"},
                 "result": json.dumps(observed204)}]
    actual204 = {"task_type": "RETRIEVE", "status": "SUCCESS",
                 "retrieved_data": answer204}
    _validate_task196_204_orders_source(204, source, trace204, actual204)
    source["latest_complete_order_items"][2]["price"] = "40.0000"
    with pytest.raises(ValueError, match="task 204"):
        _validate_task196_204_orders_source(204, source, trace204, actual204)


@pytest.mark.parametrize(("task_id", "count"), [(196, 9), (204, 7)])
def test_task196_204_old_exact_actions_are_not_official_passes(
    task_id: int, count: int
) -> None:
    fixed_trace = [
        {"index": index, "arguments": {"action": f"step-{index}"},
         "tool_status": "success"}
        for index in range(1, count + 1)
    ]
    old_trace = [dict(step) for step in fixed_trace]
    receipt = {
        "schema": "remote_exact_frozen_actions_diagnostic/1",
        "task_id": task_id,
        "status": "exact_actions_complete",
        "completed_action_count": count,
        "frozen_action_count": count,
        "official_evaluation": "not_run",
        "site_reset": False,
        "promotable": False,
        "trace_sha256": "a" * 64,
        "input_sha256": {
            "binary": "3a967a1280ffe434e678b79ec24238701cbf708154dbdd490f8b9d78d03032ff"
        },
    }
    _validate_task196_204_old_actions(
        task_id, receipt, "a" * 64, old_trace, fixed_trace
    )
    receipt["official_evaluation"] = "passed"
    with pytest.raises(ValueError, match="old exact actions"):
        _validate_task196_204_old_actions(
            task_id, receipt, "a" * 64, old_trace, fixed_trace
        )


def _write(path: Path, value: object) -> dict[str, str]:
    path.write_text(json.dumps(value), encoding="utf-8")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_diagnostic_evidence_requires_matching_regular_file(tmp_path: Path) -> None:
    ref = _write(tmp_path / "receipt.json", {"status": "pass"})
    assert _verified_digest(ref) == ref["sha256"]
    (tmp_path / "receipt.json").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        _verified_digest(ref)
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "receipt.json")
    with pytest.raises(ValueError, match="symlinked"):
        _verified_digest({"path": str(link), "sha256": ref["sha256"]})


def test_qualified_action_trace_rejects_missing_duplicate_or_failed_core_steps() -> None:
    trace = [
        {"index": 1, "tool_status": "success"},
        {"index": "1a", "status": "success"},
        {"index": 2, "status": "success"},
        {"index": 3, "tool_status": "success"},
    ]
    assert _qualified_trace_count(trace, 465, 3) == (3, 1)
    with pytest.raises(ValueError, match="incomplete"):
        _qualified_trace_count(trace[:2], 465, 3)
    with pytest.raises(ValueError, match="duplicated"):
        _qualified_trace_count([*trace, {"index": 2, "status": "success"}], 465, 3)
    failed = [*trace]
    failed[-1] = {"index": 3, "tool_status": "error"}
    assert _qualified_trace_count(failed, 465, 3) == (2, 1)


def test_task66_layout_finding_requires_identical_historical_target_and_geometry() -> None:
    selector = 'a[href="/f/books"]'
    receipt = {
        "schema": "moli_same_page_visible_geometry_diagnostic/1",
        "selector": selector,
        "page": "http://example.test/forums",
        "results": {
            "moli_e688_1_1_6": {
                "binary_reported_version": "moli 1.1.6",
                "http_status": 200,
                "ready_state": "complete",
                "anchor_count": 1,
                "playwright_visible_count": 0,
                "geometry": {"outerHTML": "<a>books</a>", "boxes": [{"width": 0}]},
            },
            "moli_fixed_af808": {
                "binary_reported_version": "moli 1.1.7",
                "binary_sha256": LATEST_BINARY_SHA256,
                "http_status": 200,
                "ready_state": "complete",
                "anchor_count": 1,
                "playwright_visible_count": 1,
                "geometry": {"outerHTML": "<a>books</a>", "boxes": [{"width": 42}]},
            },
        },
    }
    trace = [
        {},
        {},
        {"result": 'a href="/f/books"'},
        {"result": 'waiting for locator("a[href=\\"/f/books\\"]:visible")', "tool_status": "error"},
    ]
    _validate_task66_geometry_diagnostic(receipt, trace)
    receipt["results"]["moli_e688_1_1_6"]["geometry"]["boxes"][0]["width"] = 42
    with pytest.raises(ValueError, match="fragment geometry"):
        _validate_task66_geometry_diagnostic(receipt, trace)
    receipt["results"]["moli_e688_1_1_6"]["geometry"]["boxes"][0]["width"] = 0
    trace[2]["result"] = 'a href="/f/games"'
    with pytest.raises(ValueError, match="historical selector"):
        _validate_task66_geometry_diagnostic(receipt, trace)


@pytest.mark.parametrize(
    ("task_id", "forum"),
    [(66, "books"), (404, "books"), (595, "space"), (620, "relationship_advice"),
     (640, "books"), (645, "books")],
)
def test_forum_layout_finding_binds_each_original_selector(task_id: int, forum: str) -> None:
    selector = f'a[href="/f/{forum}"]'
    observation = {
        "selector": selector,
        "http_status": 200,
        "ready_state": "complete",
        "anchor_count": 1,
        "playwright_visible_count": 0,
        "geometry": {"outerHTML": f'<a href="/f/{forum}">forum</a>', "boxes": [{"width": 0}]},
    }
    receipt = {
        "schema": "moli_forum_link_visible_geometry_batch/1",
        "read_only": True,
        "page": "http://example.test/forums",
        "selectors": {forum: selector},
        "results": {
            "moli_e688_1_1_6": {
                "binary_reported_version": "moli 1.1.6",
                "http_status": 200,
                "ready_state": "complete",
                "observations": {forum: observation},
            },
            "moli_fixed_af808": {
                "binary_reported_version": "moli 1.1.7",
                "binary_sha256": LATEST_BINARY_SHA256,
                "http_status": 200,
                "ready_state": "complete",
                "observations": {
                    forum: {
                        **observation,
                        "playwright_visible_count": 1,
                        "geometry": {
                            "outerHTML": observation["geometry"]["outerHTML"],
                            "boxes": [{"width": 48}],
                        },
                    }
                },
            },
        },
    }
    trace = [
        {}, {},
        {"result": f'a href="/f/{forum}"'},
        {"result": f'waiting for locator("{selector.replace(chr(34), chr(92) + chr(34))}:visible")',
         "tool_status": "error"},
    ]
    _validate_forum_link_geometry_diagnostic(task_id, receipt, trace)
    fixed_boxes = receipt["results"]["moli_fixed_af808"]["observations"][forum]["geometry"]["boxes"]
    fixed_boxes[0]["width"] = 0
    with pytest.raises(ValueError, match="geometry did not reproduce"):
        _validate_forum_link_geometry_diagnostic(task_id, receipt, trace)


@pytest.mark.parametrize(
    ("task_id", "forum", "href"),
    [(29, "DIY", "/f/DIY/new"), (714, "gadgets", "/f/gadgets/top?t=day")],
)
def test_sort_menu_layout_binds_open_menu_and_original_target(
    task_id: int, forum: str, href: str
) -> None:
    selector = f'a[href="{href}"]'
    observation = {
        "http_status": 200,
        "ready_state": "complete",
        "page_url": f"http://example.test/f/{forum}",
        "selector": selector,
        "anchor_count": 1,
        "before_menu_visible_count": 0,
        "menu_button_count": 1,
        "playwright_visible_count": 0,
        "geometry": {"outerHTML": f'<a href="{href}">link</a>', "boxes": [{"width": 0}]},
    }
    receipt = {
        "schema": "moli_reddit_sort_link_geometry_diagnostic/1",
        "read_only": True,
        "menu_action": {
            "selector": 'button[aria-label="Sort by: Hot"]:visible',
            "semantics": "DOM element.click, then 500 ms wait",
        },
        "scenarios": {str(task_id): {"path": f"/f/{forum}", "selector": selector}},
        "results": {
            "moli_e688_1_1_6": {
                "binary_reported_version": "moli 1.1.6",
                "binary_sha256": "3a967a1280ffe434e678b79ec24238701cbf708154dbdd490f8b9d78d03032ff",
                "observations": {str(task_id): observation},
            },
            "moli_fixed_af808": {
                "binary_reported_version": "moli 1.1.7",
                "binary_sha256": LATEST_BINARY_SHA256,
                "observations": {
                    str(task_id): {
                        **observation,
                        "playwright_visible_count": 1,
                        "geometry": {
                            "outerHTML": observation["geometry"]["outerHTML"],
                            "boxes": [{"width": 138}],
                        },
                    }
                },
            },
        },
    }
    trace = [{}, {}, {}, {}, {
        "tool_status": "success",
        "arguments": {"selector": 'button[aria-label="Sort by: Hot"]:visible'},
    }, {"result": f'a href="{href}"'}, {
        "tool_status": "error",
        "result": f'waiting for locator("{selector.replace(chr(34), chr(92) + chr(34))}:visible")',
    }]
    _validate_sort_menu_geometry_diagnostic(task_id, receipt, trace)
    receipt["results"]["moli_fixed_af808"]["observations"][str(task_id)][
        "geometry"
    ]["boxes"][0]["width"] = 0
    with pytest.raises(ValueError, match="did not reproduce"):
        _validate_sort_menu_geometry_diagnostic(task_id, receipt, trace)


@pytest.mark.parametrize(
    ("task_id", "href"),
    [(314, "/primer/design"), (349, "/byteblaze/gimmiethat.space")],
)
def test_gitlab_avatar_layout_binds_duplicate_visible_match(
    task_id: int, href: str
) -> None:
    selector = f'a[href="{href}"]'
    avatar = {
        "outerHTML": f'<a class="project" href="{href}"><div>D</div></a>',
        "rect": {"width": 0},
    }
    title = {"outerHTML": f'<a href="{href}">Title</a>', "rect": {"width": 87}}
    item = {"selector": selector, "dom_count": 2, "visible_count": 1,
            "targets": [avatar, title]}
    receipt = {
        "schema": "moli_gitlab_duplicate_link_geometry_diagnostic/1",
        "read_only": True,
        "targets": {str(task_id): selector},
        "results": {
            "moli_e688_1_1_6": {
                "binary_sha256": "3a967a1280ffe434e678b79ec24238701cbf708154dbdd490f8b9d78d03032ff",
                "page_url": "http://example.test/",
                "viewport": {"width": 1920, "height": 1080, "scale": 1},
                "observations": {str(task_id): item},
            },
            "moli_fixed_af808": {
                "binary_sha256": LATEST_BINARY_SHA256,
                "page_url": "http://example.test/",
                "viewport": {"width": 1920, "height": 1080, "scale": 1},
                "observations": {str(task_id): {
                    **item, "visible_count": 2,
                    "targets": [{**avatar, "rect": {"width": 64}}, title],
                }},
            },
        },
    }
    trace = [
        {"result": f'a href="{href}"'},
        {"tool_status": "error", "result": (
            f'waiting for locator(":nth-match({selector}:visible, 2)")'
            .replace('"', '\\"')
        )},
    ]
    _validate_gitlab_avatar_geometry_diagnostic(task_id, receipt, trace)
    receipt["results"]["moli_fixed_af808"]["observations"][str(task_id)][
        "targets"
    ][0]["rect"]["width"] = 0
    with pytest.raises(ValueError, match="did not reproduce"):
        _validate_gitlab_avatar_geometry_diagnostic(task_id, receipt, trace)


def test_diagnostic_index_rejects_symlinked_parent(tmp_path: Path) -> None:
    source = tmp_path / "index.json"
    source.write_text("{}", encoding="utf-8")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="missing or symlinked"):
        load_diagnostic_closure(linked_parent / source.name, old_failed_tasks=set())


def test_diagnostic_closure_is_bounded_and_public_safe(tmp_path: Path) -> None:
    receipt = _write(tmp_path / "receipt.json", {"local_path": str(tmp_path), "status": "pass"})
    ids = sorted(CURRENT_TASKS | set(range(1000, 1027)))
    prior = {
        "tasks": [
            {
                "task_id": task_id,
                "original_status": "fail",
                "history_receipt_versions": [receipt],
                "b96f_diagnostic_closure": {
                    "completed_pair": receipt,
                    "independent_audit": receipt,
                    "physical_results": [
                        {
                            "browser": browser,
                            "receipt": receipt["path"],
                            "receipt_sha256": receipt["sha256"],
                            "binary_sha256": "a" * 64,
                            "raw_status": "diagnostic_current_retrieval_pass",
                        }
                        for browser in ("chromium", "moli")
                    ],
                },
            }
            for task_id in ids
        ]
    }
    prior_ref = _write(tmp_path / "prior.json", prior)
    index = {
        "schema": "original36_diagnostic_closure_index/5",
        "promotable": False,
        "formal_release": False,
        "cohort_certified": False,
        "official_scores_modified": False,
        "original_task_count": 36,
        "diagnostically_closed_count": 36,
        "inherited_old_binary_closed_count": 27,
        "current_b96_diagnostically_closed_task_ids": sorted(CURRENT_TASKS),
        "predecessor": prior_ref,
        "tasks": [
            {
                "task_id": task_id,
                "task_type": "RETRIEVE",
                "b96_current_task_validation": task_id in CURRENT_TASKS,
                "closure_evidence": [receipt],
                "diagnosis": {"category": "observer", "code_or_process": "shared observer"},
                "raw_results": [
                    {
                        "browser": browser,
                        "receipt": receipt,
                        "binary_sha256": "a" * 64,
                        "status": "diagnostic_current_retrieval_pass",
                    }
                    for browser in ("chromium", "moli")
                ],
                "downstream_results": [
                    {"browser": browser, "audit": receipt, "accepted": True}
                    for browser in ("chromium", "moli")
                ],
            }
            for task_id in ids
        ],
    }
    path = tmp_path / "index.json"
    _write(path, index)
    model = load_diagnostic_closure(path, old_failed_tasks=set(ids))
    assert len(model["entries"]) == 36
    assert (model["current_b96"], model["inherited_mixed_build"]) == (9, 27)
    assert str(tmp_path) not in json.dumps(model)
    assert model["official_scores_changed"] is False
    with pytest.raises(ValueError, match="does not match"):
        load_diagnostic_closure(path, old_failed_tasks={1})


def test_diagnostic_pages_keep_old_verdict_and_bilingual_routes(tmp_path: Path) -> None:
    _assets(tmp_path)
    (tmp_path / "en").mkdir()
    for lang_root in (tmp_path, tmp_path / "en"):
        for landing in ("index.html", "fixes.html"):
            (lang_root / landing).write_text("<html><main>existing</main></html>", encoding="utf-8")
    model = {
        "latest_moli": {
            "source_head": LATEST_SOURCE_HEAD,
            "source_diff_sha256": "f" * 64,
            "binary_sha256": "a" * 64,
            "observations": [
                {
                    "task_id": 29,
                    "completed_actions": 9,
                    "action_count": 9,
                    "official_rule_score": 1.0,
                    "receipt_sha256": "c" * 64,
                    "completion_sha256": "d" * 64,
                    "scope_note": "current_source_retrieval",
                }
            ],
        },
        "entries": [
            {
                "task_id": 108,
                "task_type": "RETRIEVE",
                "generation": "current_b96",
                "finding": ("日期筛选", "Date filter", "中文原因", "English finding"),
                "evidence_sha256": ["a" * 64],
                "observations": [
                    {
                        "browser": "moli",
                        "binary_sha256": "a" * 64,
                        "receipt_sha256": "b" * 64,
                        "audit_sha256": "c" * 64,
                        "status": "diagnostic_current_retrieval_incomplete",
                        "completed_actions": 10,
                        "action_count": 10,
                        "official_rule_score": None,
                        "downstream_accepted": True,
                    }
                ],
            },
            {
                "task_id": 29,
                "task_type": "RETRIEVE",
                "generation": "inherited_mixed_build",
                "finding": None,
                "evidence_sha256": ["b" * 64],
                "observations": [],
            },
            {
                "task_id": 313,
                "task_type": "RETRIEVE",
                "generation": "current_b96",
                "finding": CURRENT_FINDINGS[313],
                "owning_process": "retrieval acceptance",
                "owning_process_zh": "取数验收",
                "evidence_sha256": ["c" * 64],
                "observations": [
                    {
                        "browser": "moli",
                        "binary_sha256": "d" * 64,
                        "receipt_sha256": "e" * 64,
                        "status": "diagnostic_current_retrieval_incomplete",
                        "completed_actions": 7,
                        "action_count": 7,
                        "official_rule_score": None,
                        "downstream_accepted": True,
                    }
                ],
            },
        ],
    }
    _write_diagnostic_closure(
        tmp_path,
        model,
        moli_result_keys={108: "moli-1.1.3-108", 29: "moli-1.1.3-29", 313: "moli-1.1.3-313"},
    )
    zh = (tmp_path / "diagnostics.html").read_text(encoding="utf-8")
    en = (tmp_path / "en/diagnostics.html").read_text(encoding="utf-8")
    assert "Chromium 90/90、Moli 54/90" in zh
    assert "Chromium 90/90 and Moli 54/90" in en
    assert 'href="diagnostic/108.html"' in zh and 'href="diagnostic/108.html"' in en
    assert 'href="../result/moli-1.1.3-108.html"' in (tmp_path / "diagnostic/108.html").read_text(
        encoding="utf-8"
    )
    assert "no new code-level cause" in (tmp_path / "en/diagnostic/29.html").read_text(
        encoding="utf-8"
    )
    assert "早期有限范围复核接受；当前资格未通过" in (
        tmp_path / "diagnostic/313.html"
    ).read_text(encoding="utf-8")
    assert "Earlier limited-scope audit accepted; current qualification failed" in (
        tmp_path / "en/diagnostic/313.html"
    ).read_text(encoding="utf-8")
    assert "diagnostics.html" in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Moli 1.1.7 定点诊断" in (tmp_path / "diagnostic/29.html").read_text(
        encoding="utf-8"
    )
    detail_json = json.loads((tmp_path / "data/diagnostic/29.json").read_text(encoding="utf-8"))
    assert detail_json["latest_moli"]["observation"]["official_rule_score"] == 1.0
    assert detail_json["latest_moli"]["source_head"] == LATEST_SOURCE_HEAD


def test_fixed_binary_diagnostic_keeps_rule_semantics_and_formal_score_separate(
    tmp_path: Path,
) -> None:
    _assets(tmp_path)
    (tmp_path / "en").mkdir()
    for lang_root in (tmp_path, tmp_path / "en"):
        for landing in ("index.html", "fixes.html"):
            (lang_root / landing).write_text("<html><main>existing</main></html>", encoding="utf-8")
    entries = [
        {
            "task_id": task_id,
            "task_type": "RETRIEVE" if task_id != 465 else "MUTATE",
            "generation": "inherited_mixed_build",
            "finding": None,
            "evidence_sha256": ["a" * 64],
            "observations": [],
        }
        for task_id in (66, 243, 313, 404, 465)
    ]
    observations = []
    for task_id, semantic_status, reason in (
        (66, "limited", "Independent task semantics were not checked"),
        (243, "unverified", "Guest identity is unproven"),
        (313, "limited", "The site-wide absence was not exhaustively checked"),
        (404, "limited", "Independent task semantics were not checked"),
        (465, "verified", "The wishlist item persisted"),
    ):
        observations.append(
            {
                "task_id": task_id,
                "qualified_action_count": 8,
                "completed_qualified_actions": 8,
                "frozen_original_action_count": 8,
                "action_completion": "complete_qualified",
                "canonical_actions_replayed": False,
                "input_revision": {"candidate_sha256": "b" * 64},
                "official_rule_score": 1.0,
                "evaluation_mode": (
                    "source_derived_synthetic"
                    if task_id in (243, 313)
                    else "actual_trajectory_output"
                ),
                "semantic_verification": {
                    "status": semantic_status,
                    "reason_zh": reason,
                    "reason_en": reason,
                },
                    "cause": {
                        "category": (
                            "moli_forum_link_layout_reproduced"
                            if task_id in (66, 404)
                            else "site_script_readiness" if task_id == 465 else "dataset_evaluator"
                        ),
                        "summary_zh": "已绑定证据的定位",
                    "summary_en": "Evidence-bound finding",
                    "fix_zh": "已验证的修复",
                    "fix_en": "Validated repair",
                },
                "evidence_sha256": {"receipt": "b" * 64},
                **(
                    {
                        "event_timeline": [
                            {
                                "label_zh": "就绪",
                                "label_en": "Ready",
                                "value_zh": "13 毫秒",
                                "value_en": "13 ms",
                            }
                        ],
                        "timeline_evidence_sha256": "c" * 64,
                    }
                    if task_id == 465 else {}
                ),
            }
        )
    task243 = next(row for row in observations if row["task_id"] == 243)
    task243["identity_neutral_ui"] = {
        "qualified_actions": 14,
        "customer_rows": 70,
        "exact_name_matches": 1,
        "author_identity_claimed": False,
        "official_score_effect": "none",
        "receipt_sha256": "d" * 64,
        "grid_sha256": "e" * 64,
    }
    task243["identity_source_audit"] = {
        "review_id": 353,
        "review_customer_id": None,
        "exact_name_account_count": 1,
        "matching_order_count": 0,
        "verified_author_account_link": False,
        "public_audit_sha256": "f" * 64,
        "source_audit_sha256": "0" * 64,
        "rating_vote_audit_sha256": "1" * 64,
        "source_mechanism_sha256": "2" * 64,
        "post_hoc_reference_validity": "unverifiable",
        "affected_configurations": ["chromium", "moli-1.1.3"],
    }
    model = {
        "entries": entries,
        "fixed36": {
            "source_head": LATEST_SOURCE_HEAD,
            "binary_sha256": LATEST_BINARY_SHA256,
            "complete_qualified_replays": 5,
            "official_rule_pass_tasks": 5,
            "source_derived_official_pass_tasks": 2,
            "unverifiable_references": 1,
            "semantic_counts": {"verified": 1, "limited": 3, "unverified": 1},
            "observations": observations,
        },
    }
    _write_diagnostic_closure(
        tmp_path,
        model,
        moli_result_keys={
            task_id: f"moli-1.1.3-{task_id}" for task_id in (66, 243, 313, 404, 465)
        },
    )
    zh = (tmp_path / "diagnostics.html").read_text(encoding="utf-8")
    en = (tmp_path / "en/diagnostics.html").read_text(encoding="utf-8")
    assert "Chromium 90/90、Moli 54/90" in zh
    assert "Chromium 90/90 and Moli 54/90" in en
    assert "5/36 条合格候选动作全部完成" in zh
    assert "5/36 qualified candidate action sequences completed" in en
    assert "1 条题目的邮箱参考答案无法由站点记录验证" in zh
    assert "1 task has an email reference" in en
    assert en.count("<td>One fixed Moli candidate</td>") == 5
    assert "Earlier diagnostic records" in en
    fix_log = (tmp_path / "en/fixes.html").read_text(encoding="utf-8")
    assert "Findings from affected-task retests" in fix_log
    assert 'href="diagnostic/243.html"' in fix_log
    assert 'href="diagnostic/66.html"' in fix_log
    assert 'href="diagnostic/404.html"' in fix_log
    assert fix_log.count("forum-list links appeared") == 1
    task243_detail = (tmp_path / "en/diagnostic/243.html").read_text(encoding="utf-8")
    assert "Complete UI retrieval (revised task)" in task243_detail
    assert "Guest-review authorship remains unverified" in task243_detail
    assert "customer ID is null" in task243_detail
    assert "same-name accounts" in task243_detail
    assert "linked orders" in task243_detail
    assert "No verifiable author-to-account link exists" in task243_detail
    assert "does not collect email" in task243_detail
    for task_id, semantic in (
        (66, "Limited scope"), (243, "Unverified"), (313, "Limited scope"),
        (404, "Limited scope"), (465, "Verified"),
    ):
        detail = (tmp_path / f"en/diagnostic/{task_id}.html").read_text(encoding="utf-8")
        assert semantic in detail
        assert "Not included" in detail
        assert "Fixed Moli repair candidate build" in detail
        assert "not the frozen original actions" in detail
    assert "Reconstructed from events in the bound receipts" in (
        tmp_path / "en/diagnostic/465.html"
    ).read_text(encoding="utf-8")


def _latest_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    predecessor = _write(tmp_path / "v5.json", {"schema": "original36_diagnostic_closure_index/5"})
    binary = _write(tmp_path / "moli", {"binary": "fixture"})
    monkeypatch.setattr(closure_module, "LATEST_BINARY_SHA256", binary["sha256"])
    release = _write(
        tmp_path / "release.json",
        {
            "schema": "moli_latest_main_release_diagnostic/1",
            "source_head": LATEST_SOURCE_HEAD,
            "binary_sha256": binary["sha256"],
            "source_diff_sha256_before": "a" * 64,
            "source_diff_sha256_after": "a" * 64,
            "source_changed_during_check": False,
            "exit_code": 0,
        },
    )
    candidates = [
        {
            "task_id": task_id,
            **_write(
                tmp_path / f"{task_id}-candidate.json",
                {
                    "task_id": task_id,
                    "retrieval_contract": {
                        "schema": "lex_replay_current_run_retrieval/3",
                        "semantic_reference": {"kind": "reviewed_value_set"},
                    }
                    if task_id == 163 else {},
                },
            ),
        }
        for task_id in (29, 66, 163, 173)
    ]
    manifest = _write(
        tmp_path / "manifest.json",
        {
            "schema": "moli_latest_main_retrieval_four_diagnostic/1",
            "promotable": False,
            "source_head": LATEST_SOURCE_HEAD,
            "moli_binary_sha256": binary["sha256"],
            "task_ids": [29, 66, 163, 173],
            "candidate_bindings": candidates,
            "retry": False,
        },
    )
    archive = _write(tmp_path / "archive.json", {"archived": True})
    tasks = []
    for task_id in sorted(LATEST_TASKS):
        evaluation = _write(
            tmp_path / f"{task_id}-evaluation.json", {"task_id": task_id, "score": 1.0}
        )
        shared = _write(tmp_path / f"{task_id}-trace.json", {"task_id": task_id})
        evidence = {
            "trace.json": shared,
            "network.har": shared,
            "evaluation.json": evaluation,
            "candidate-input.json": shared,
        }
        evidence[
            "completion-observation.json" if task_id == 389 else "retrieval-acceptance.json"
        ] = shared
        status = (
            "diagnostic_mutation_completion_pass"
            if task_id == 389 else "diagnostic_current_retrieval_pass"
        )
        receipt = _write(
            tmp_path / f"{task_id}-receipt.json",
            {
                "task_id": task_id,
                "binary_sha256": binary["sha256"],
                "status": status,
                "official_score": 1.0,
                "completed_action_count": 7,
                "action_count": 7,
                "process_status": "pass",
                "recorded_response_used": False,
                "evidence": evidence,
            },
        )
        completion = _write(
            tmp_path / f"{task_id}-completion.json",
            {"task_id": task_id, "status": status, "official_score": 1.0},
        )
        tasks.append({"task_id": task_id, "receipt": receipt, "completion": completion})
    index = {
        "schema": "original36_latest_moli_diagnostic_index/6",
        "promotable": False,
        "formal_release": False,
        "cohort_certified": False,
        "official_scores_modified": False,
        "predecessor": predecessor,
        "source_receipt": release,
        "binary": binary,
        "archive": archive,
        "retrieval_manifest": manifest,
        "tasks": tasks,
    }
    monkeypatch.setattr(
        closure_module,
        "load_diagnostic_closure",
        lambda _path, old_failed_tasks: {
            "source_sha256": predecessor["sha256"],
            "entries": [],
            "original_failures": 36,
            "inherited_mixed_build": 27,
            "current_b96": 9,
        },
    )
    return index


def test_latest_five_are_separate_and_all_named_evidence_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _latest_fixture(tmp_path, monkeypatch)
    model = _load_latest_index(index, "c" * 64, old_failed_tasks=set())
    latest = model["latest_moli"]
    assert model["original_failures"] == 36
    assert len(latest["observations"]) == 5
    assert latest["formal_cohort"] is False
    assert latest["source_diff_sha256"] == "a" * 64
    assert next(row for row in latest["observations"] if row["task_id"] == 163)[
        "scope_note"
    ] == "predeclared_shared_v3_correction"
    assert next(row for row in latest["observations"] if row["task_id"] == 389)[
        "scope_note"
    ] == "read_only_completion_observation"
    assert str(tmp_path) not in json.dumps(model)
    (tmp_path / "29-trace.json").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        _load_latest_index(index, "c" * 64, old_failed_tasks=set())


def test_latest_five_rejects_promotion_and_wrong_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _latest_fixture(tmp_path, monkeypatch)
    index["cohort_certified"] = True
    with pytest.raises(ValueError, match="cannot claim certification"):
        _load_latest_index(index, "c" * 64, old_failed_tasks=set())
    index["cohort_certified"] = False
    index["tasks"] = index["tasks"][:-1]
    with pytest.raises(ValueError, match="exactly five"):
        _load_latest_index(index, "c" * 64, old_failed_tasks=set())


def test_latest_diagnostic_page_keeps_later_receipt_separate() -> None:
    latest = {
        "source_head": LATEST_SOURCE_HEAD,
        "source_diff_sha256": "a" * 64,
        "binary_sha256": "b" * 64,
        "observations": [
            {
                "task_id": 389,
                "completed_actions": 10,
                "action_count": 10,
                "official_rule_score": 1.0,
                "receipt_sha256": "c" * 64,
                "completion_sha256": "d" * 64,
                "scope_note": "read_only_completion_observation",
            }
        ],
    }
    zh = _latest_moli_task_section("zh", latest, 389)
    en = _latest_moli_task_section("en", latest, 389)
    assert "Moli 1.1.7 定点诊断" in zh
    assert "只读完成观察" in zh
    assert "does not replace the historical score" in en
    assert LATEST_SOURCE_HEAD in zh and "c" * 64 in en
    assert _latest_moli_task_section("zh", latest, 29) == ""
