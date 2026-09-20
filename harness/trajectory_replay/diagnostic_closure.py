"""Public-safe, hash-bound view of the original 36 replay failures.

The closure index is diagnostic evidence, never a replacement result cohort.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from webarena_verified.types.task import WebArenaSite

from .remote_runtime import load_remote_sites, rebind_remote_calls
from .retrieval_acceptance import build_actual_response, digest

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
CURRENT_TASKS = {108, 196, 204, 243, 313, 314, 349, 464, 784}
LATEST_TASKS = {29, 66, 163, 173, 389}
LATEST_SOURCE_HEAD = "b7ba9d47d1bfaf7c8d8530be52c5775cf5ceeada"
LATEST_BINARY_SHA256 = "af808b3f7bb5dd0604262bba95cecdf06ba18d9547a169c1443bfbb1255b4397"

# These are report-authored explanations of the current evidence, not copied
# from internal receipts. Historical classifications are not promoted here.
CURRENT_FINDINGS = {
    108: (
        "日期筛选与观察输入",
        "Date filter and observation input",
        "上限日期没有可靠触发，修订后的输入和完整当前订单范围经双浏览器验证。",
        "The upper date bound was not reliably applied. Revised input and the complete current "
        "order scope were checked on both browsers.",
    ),
    196: (
        "共享数据请求协议",
        "Shared data request contract",
        "共享观察器未明确请求 JSON，服务器返回 HTML；修订请求头和完整性检查后验证。",
        "The shared observer did not explicitly request JSON and received HTML; "
        "the revised request and completeness checks were verified.",
    ),
    204: (
        "共享数据请求协议",
        "Shared data request contract",
        "同类 JSON 协商问题；修订后核对了完整订单来源及页面投影。",
        "The same JSON negotiation defect; the complete order source and page projection "
        "were checked after correction.",
    ),
    243: (
        "身份与验收合同",
        "Identity and acceptance contract",
        "原身份匹配规则未声明。较早诊断曾按完整客户集合中的唯一全名推断身份；这不是访客作者身份认证，当前候选尚未通过独立资格核验。",
        "The original identity-join rule was undeclared. An earlier diagnostic inferred identity "
        "from a unique full-name match in the customer set. That is not authenticated guest "
        "authorship, and the current candidate has not passed independent qualification.",
    ),
    313: (
        "空结果验收合同",
        "Empty-result acceptance contract",
        "独立来源核对显示门店电话配置为空，全部 CMS 页面和 Contact Us 客服入口未列电话号码；"
        "这只证明公开客服入口范围，不证明全站不存在第三方电话；当前浏览器观察和验收器只核查这一有限范围。",
        "Independent source checks found an empty store-phone setting and no phone number in "
        "any CMS page or the Contact Us support surface. This covers published store-support "
        "surfaces, not third-party numbers across the site; the current browser observation "
        "and scorer enforce only that bounded scope.",
    ),
    314: (
        "观察范围",
        "Observation scope",
        "重新核对项目默认分支和完整贡献者范围；本证据不能单独归因于浏览器引擎。",
        "The project default branch and complete contributor scope were rechecked; "
        "this evidence does not isolate a browser-engine cause.",
    ),
    349: (
        "成员范围与身份",
        "Member scope and identity",
        "按完整有效成员列表与数字用户 ID 核对，避免仅按显示名排除当前用户。",
        "The complete effective-member list and numeric user IDs were checked, "
        "avoiding display-name-only exclusion of the current user.",
    ),
    464: (
        "提交后持久化",
        "Post-submit persistence",
        "浏览器动作之外，另以新请求核对保存后的商品数据；原始保守完成标签仍保留。",
        "A fresh request checked persisted product data after the browser actions; "
        "the original conservative completion label remains retained.",
    ),
    784: (
        "贡献者数据来源",
        "Contributor data source",
        "旧贡献者接口忽略分支参数并截断于 2000 次提交；新验证固定 master 提交并遍历完整父链。",
        "The old contributors endpoint ignored the branch parameter and capped at 2,000 commits. "
        "The new check pins the master commit and traverses the full parent graph.",
    ),
}

CURRENT_PROCESS_ZH = {
    108: "固定轨迹的页面就绪、日期输入与选择器",
    196: "共同观察器的 JSON 请求头；Magento Context.php",
    204: "共同观察器的 JSON 请求头；Magento Context.php",
    243: "retrieval_acceptance.py 的身份匹配与验收规则",
    313: "retrieval_acceptance.py 的空结果验收规则",
    314: "共同观察器的项目及默认分支范围",
    349: "共同观察器的成员范围与用户 ID",
    464: "保存后的独立请求与数据持久化核对",
    784: "共同观察器的贡献者接口与提交遍历",
}


def _validate_task66_geometry_diagnostic(
    receipt: dict[str, Any], trace: list[dict[str, Any]]
) -> None:
    """Bind the reproduced zero-width mechanism to the exact historical selector."""
    if (
        receipt.get("schema") != "moli_same_page_visible_geometry_diagnostic/1"
        or receipt.get("selector") != 'a[href="/f/books"]'
        or receipt.get("page", "").split("/", 3)[-1] != "forums"
        or len(trace) < 4
        or 'a href="/f/books"' not in trace[2].get("result", "")
        or 'a[href=\\"/f/books\\"]:visible' not in trace[3].get("result", "")
        or trace[3].get("tool_status") != "error"
    ):
        raise ValueError("task 66 historical selector does not match geometry diagnostic")
    results = receipt.get("results") or {}
    old = results.get("moli_e688_1_1_6") or {}
    fixed = results.get("moli_fixed_af808") or {}
    if (
        old.get("binary_reported_version") != "moli 1.1.6"
        or fixed.get("binary_reported_version") != "moli 1.1.7"
        or fixed.get("binary_sha256") != LATEST_BINARY_SHA256
        or old.get("http_status") != 200
        or fixed.get("http_status") != 200
        or old.get("ready_state") != "complete"
        or fixed.get("ready_state") != "complete"
        or old.get("anchor_count") != fixed.get("anchor_count") != 1
        or old.get("playwright_visible_count") != 0
        or fixed.get("playwright_visible_count") != 1
    ):
        raise ValueError("task 66 same-page visible outcome differs")
    old_geometry = old.get("geometry") or {}
    fixed_geometry = fixed.get("geometry") or {}
    old_boxes = old_geometry.get("boxes") or []
    fixed_boxes = fixed_geometry.get("boxes") or []
    if (
        not old_boxes
        or not fixed_boxes
        or old_geometry.get("outerHTML") != fixed_geometry.get("outerHTML")
        or any(box.get("width") != 0 for box in old_boxes)
        or not any(box.get("width", 0) > 0 for box in fixed_boxes)
    ):
        raise ValueError("task 66 fragment geometry did not reproduce zero-to-positive width")


_FORUM_LINK_FAILURES = {
    66: "books",
    404: "books",
    595: "space",
    620: "relationship_advice",
    640: "books",
    645: "books",
}

_SORT_MENU_FAILURES = {
    29: ("DIY", 'a[href="/f/DIY/new"]'),
    714: ("gadgets", 'a[href="/f/gadgets/top?t=day"]'),
}

_GITLAB_AVATAR_FAILURES = {
    314: "/primer/design",
    349: "/byteblaze/gimmiethat.space",
}


def _validate_gitlab_avatar_geometry_diagnostic(
    task_id: int, receipt: dict[str, Any], trace: list[dict[str, Any]]
) -> None:
    href = _GITLAB_AVATAR_FAILURES.get(task_id)
    if href is None:
        raise ValueError("GitLab avatar finding applies only to bound failures")
    selector = f'a[href="{href}"]'
    if (
        receipt.get("schema") != "moli_gitlab_duplicate_link_geometry_diagnostic/1"
        or receipt.get("read_only") is not True
        or receipt.get("targets", {}).get(str(task_id)) != selector
        or len(trace) < 2
        or f'a href="{href}"' not in trace[0].get("result", "")
        or trace[1].get("tool_status") != "error"
        or f":nth-match({selector}:visible, 2)".replace('"', '\\"')
        not in trace[1].get("result", "")
    ):
        raise ValueError(f"task {task_id} historical GitLab target differs")
    results = receipt.get("results") or {}
    old = results.get("moli_e688_1_1_6") or {}
    fixed = results.get("moli_fixed_af808") or {}
    old_item = (old.get("observations") or {}).get(str(task_id)) or {}
    fixed_item = (fixed.get("observations") or {}).get(str(task_id)) or {}
    old_targets = old_item.get("targets") or []
    fixed_targets = fixed_item.get("targets") or []
    if (
        old.get("binary_sha256")
        != "3a967a1280ffe434e678b79ec24238701cbf708154dbdd490f8b9d78d03032ff"
        or fixed.get("binary_sha256") != LATEST_BINARY_SHA256
        or old.get("page_url") != fixed.get("page_url")
        or not old.get("page_url", "").endswith("/")
        or old.get("viewport") != fixed.get("viewport")
        or old.get("viewport") != {"width": 1920, "height": 1080, "scale": 1}
        or old_item.get("selector") != fixed_item.get("selector") != selector
        or old_item.get("dom_count") != fixed_item.get("dom_count") != 2
        or old_item.get("visible_count") != 1
        or fixed_item.get("visible_count") != 2
        or len(old_targets) != 2
        or len(fixed_targets) != 2
        or any(
            a.get("outerHTML") != b.get("outerHTML")
            for a, b in zip(old_targets, fixed_targets, strict=True)
        )
        or 'class="project"' not in old_targets[0].get("outerHTML", "")
        or "<div" not in old_targets[0].get("outerHTML", "")
        or old_targets[0].get("rect", {}).get("width") != 0
        or fixed_targets[0].get("rect", {}).get("width", 0) <= 0
        or old_targets[1].get("rect", {}).get("width", 0) <= 0
        or fixed_targets[1].get("rect", {}).get("width", 0) <= 0
    ):
        raise ValueError(f"task {task_id} GitLab avatar geometry did not reproduce")


def _validate_sort_menu_geometry_diagnostic(
    task_id: int, receipt: dict[str, Any], trace: list[dict[str, Any]]
) -> None:
    target = _SORT_MENU_FAILURES.get(task_id)
    if target is None:
        raise ValueError("sort-menu geometry finding applies only to bound failures")
    forum, selector = target
    href = selector.split('"')[1]
    if (
        receipt.get("schema") != "moli_reddit_sort_link_geometry_diagnostic/1"
        or receipt.get("read_only") is not True
        or receipt.get("menu_action", {}).get("selector")
        != 'button[aria-label="Sort by: Hot"]:visible'
        or receipt.get("menu_action", {}).get("semantics")
        != "DOM element.click, then 500 ms wait"
        or receipt.get("scenarios", {}).get(str(task_id))
        != {"path": f"/f/{forum}", "selector": selector}
        or len(trace) < 7
        or trace[4].get("tool_status") != "success"
        or trace[4].get("arguments", {}).get("selector")
        != 'button[aria-label="Sort by: Hot"]:visible'
        or f'a href="{href}"' not in trace[5].get("result", "")
        or trace[6].get("tool_status") != "error"
        or selector.replace('"', '\\"') + ":visible" not in trace[6].get("result", "")
    ):
        raise ValueError(f"task {task_id} historical sort-menu failure differs")
    results = receipt.get("results") or {}
    old = results.get("moli_e688_1_1_6") or {}
    fixed = results.get("moli_fixed_af808") or {}
    old_item = (old.get("observations") or {}).get(str(task_id)) or {}
    fixed_item = (fixed.get("observations") or {}).get(str(task_id)) or {}
    old_geometry = old_item.get("geometry") or {}
    fixed_geometry = fixed_item.get("geometry") or {}
    old_boxes = old_geometry.get("boxes") or []
    fixed_boxes = fixed_geometry.get("boxes") or []
    if (
        old.get("binary_reported_version") != "moli 1.1.6"
        or old.get("binary_sha256")
        != "3a967a1280ffe434e678b79ec24238701cbf708154dbdd490f8b9d78d03032ff"
        or fixed.get("binary_reported_version") != "moli 1.1.7"
        or fixed.get("binary_sha256") != LATEST_BINARY_SHA256
        or not old_item.get("page_url", "").endswith(f"/f/{forum}")
        or not fixed_item.get("page_url", "").endswith(f"/f/{forum}")
        or old_item.get("http_status") != 200
        or fixed_item.get("http_status") != 200
        or old_item.get("ready_state") != "complete"
        or fixed_item.get("ready_state") != "complete"
        or old_item.get("selector") != fixed_item.get("selector") != selector
        or old_item.get("before_menu_visible_count") != 0
        or fixed_item.get("before_menu_visible_count") != 0
        or old_item.get("menu_button_count") != 1
        or fixed_item.get("menu_button_count") != 1
        or old_item.get("anchor_count") != fixed_item.get("anchor_count") != 1
        or old_item.get("playwright_visible_count") != 0
        or fixed_item.get("playwright_visible_count") != 1
        or old_geometry.get("outerHTML") != fixed_geometry.get("outerHTML")
        or not old_boxes
        or not fixed_boxes
        or any(box.get("width") != 0 for box in old_boxes)
        or not any(box.get("width", 0) > 0 for box in fixed_boxes)
    ):
        raise ValueError(f"task {task_id} sort-menu link geometry did not reproduce")


def _validate_forum_link_geometry_diagnostic(
    task_id: int, receipt: dict[str, Any], trace: list[dict[str, Any]]
) -> None:
    label = _FORUM_LINK_FAILURES.get(task_id)
    if label is None:
        raise ValueError("forum link geometry finding applies only to bound failures")
    selector = f'a[href="/f/{label}"]'
    if (
        receipt.get("schema") != "moli_forum_link_visible_geometry_batch/1"
        or receipt.get("read_only") is not True
        or receipt.get("page", "").split("/", 3)[-1] != "forums"
        or receipt.get("selectors", {}).get(label) != selector
        or len(trace) < 4
        or f'a href="/f/{label}"' not in trace[2].get("result", "")
        or selector.replace('"', '\\"') + ":visible" not in trace[3].get("result", "")
        or trace[3].get("tool_status") != "error"
    ):
        raise ValueError(f"task {task_id} historical forum link differs")
    results = receipt.get("results") or {}
    old = results.get("moli_e688_1_1_6") or {}
    fixed = results.get("moli_fixed_af808") or {}
    old_item = (old.get("observations") or {}).get(label) or {}
    fixed_item = (fixed.get("observations") or {}).get(label) or {}
    old_geometry = old_item.get("geometry") or {}
    fixed_geometry = fixed_item.get("geometry") or {}
    old_boxes = old_geometry.get("boxes") or []
    fixed_boxes = fixed_geometry.get("boxes") or []
    if (
        old.get("binary_reported_version") != "moli 1.1.6"
        or fixed.get("binary_reported_version") != "moli 1.1.7"
        or fixed.get("binary_sha256") != LATEST_BINARY_SHA256
        or old.get("http_status") != 200
        or fixed.get("http_status") != 200
        or old.get("ready_state") != "complete"
        or fixed.get("ready_state") != "complete"
        or old_item.get("selector") != fixed_item.get("selector") != selector
        or old_item.get("anchor_count") != fixed_item.get("anchor_count") != 1
        or old_item.get("playwright_visible_count") != 0
        or fixed_item.get("playwright_visible_count") != 1
        or old_geometry.get("outerHTML") != fixed_geometry.get("outerHTML")
        or not old_boxes
        or not fixed_boxes
        or any(box.get("width") != 0 for box in old_boxes)
        or not any(box.get("width", 0) > 0 for box in fixed_boxes)
    ):
        raise ValueError(f"task {task_id} forum link geometry did not reproduce")


def _validate_task243_identity_audit(
    raw_sha256: str,
    raw: dict[str, Any],
    public: dict[str, Any],
    vote_sha256: str,
    vote: dict[str, Any],
) -> None:
    reviews = raw.get("review_rows") or []
    selected = [review for review in reviews if review.get("review_id") == 353]
    accounts = raw.get("exact_name_customer_rows") or []
    orders = raw.get("associated_order_aggregate") or {}
    vote_rows = vote.get("rating_option_vote_rows") or []
    target_votes = [row for row in vote_rows if row.get("review_id") == 353]
    vote_summary = public.get("rating_vote_audit") or {}
    if (
        raw.get("schema") != "task243_remote_read_only_identity_audit/1"
        or raw.get("task_id") != 243
        or raw.get("identity_verified") is not False
        or len(selected) != 1
        or selected[0].get("customer_id") is not None
        or selected[0].get("nickname") != "Hannah Lim"
        or len(accounts) != 1
        or accounts[0].get("firstname") != "Hannah"
        or accounts[0].get("lastname") != "Lim"
        or orders.get("matching_orders") != 0
        or public.get("schema") != "lex_replay_task_identity_source_audit/1"
        or public.get("task_id") != 243
        or public.get("source_audit_sha256") != raw_sha256
        or public.get("review_id") != 353
        or public.get("review_customer_id") is not None
        or public.get("exact_name_account_count") != 1
        or public.get("matching_order_count") != 0
        or public.get("verified_author_account_link") is not False
        or vote.get("schema") != "task243_remote_read_only_rating_vote_audit/1"
        or vote.get("task_id") != 243
        or vote.get("identity_verified") is not False
        or vote.get("tables_with_both_review_id_and_customer_id")
        != ["rating_option_vote", "review_detail"]
        or len(target_votes) != 1
        or target_votes[0].get("customer_id") is not None
        or vote_summary.get("receipt_sha256") != vote_sha256
        or vote_summary.get("review_id") != 353
        or vote_summary.get("vote_customer_id") is not None
        or vote_summary.get("review_link_tables_checked")
        != ["rating_option_vote", "review_detail"]
    ):
        raise ValueError("task 243 guest identity source audit differs")


def _validate_task243_source_mechanism_audit(audit: dict[str, Any]) -> None:
    sources = audit.get("source_code") or []
    inventory = audit.get("database_linkage_inventory") or {}
    curation = audit.get("independent_task_curation") or {}
    upstream = audit.get("upstream_dataset_comparison") or {}
    current_reviews = audit.get("current_site_review_check") or {}
    validity = audit.get("post_hoc_reference_validity") or {}
    expected_tables = {
        "rating_option_vote": ["customer_id", "review_id"],
        "review": ["review_id"],
        "review_detail": ["customer_id", "review_id"],
        "review_store": ["review_id"],
    }
    tables = {
        row.get("table"): row.get("relevant_columns")
        for row in inventory.get("tables") or []
    }
    if (
        audit.get("schema") != "trajectory_replay_task243_source_mechanism_audit/1"
        or audit.get("task_id") != 243
        or len(sources) != 2
        or sources[0].get("module_path")
        != "vendor/magento/module-review/view/frontend/templates/form.phtml"
        or sources[1].get("module_path")
        != "vendor/magento/module-review/Controller/Product/Post.php"
        or any(not SHA256.fullmatch(source.get("sha256", "")) for source in sources)
        or tables != expected_tables
        or inventory.get("review_id") != 353
        or inventory.get("review_detail_customer_id") is not None
        or inventory.get("rating_option_vote_customer_id") is not None
        or inventory.get("review_email_column_exists") is not False
        or curation.get("source_title")
        != "WONDERBREAD: A Benchmark for Evaluating Multimodal Foundation Models on Business Process Management Tasks"
        or curation.get("source_url")
        != "https://proceedings.neurips.cc/paper_files/paper/2024/file/d1fa821312040303b089ae529dbf81a6-Paper-Datasets_and_Benchmarks_Track.pdf"
        or "Appendix A.3" not in curation.get("source_location", "")
        or "not proof" not in curation.get("scope_limit", "")
        or re.fullmatch(r"[0-9a-f]{40}", upstream.get("repository_commit", "")) is None
        or not SHA256.fullmatch(upstream.get("raw_dataset_sha256", ""))
        or not SHA256.fullmatch(upstream.get("verified_dataset_sha256", ""))
        or not upstream.get("raw_dataset_url", "").startswith(
            "https://raw.githubusercontent.com/ServiceNow/webarena-verified/"
        )
        or not upstream.get("verified_dataset_url", "").startswith(
            "https://raw.githubusercontent.com/ServiceNow/webarena-verified/"
        )
        or upstream.get("raw_reference_url") != ""
        or upstream.get("raw_string_note") != ""
        or upstream.get("verified_task_revision") != 2
        or upstream.get("verified_expected_status") != "SUCCESS"
        or upstream.get("verified_expected_retrieved_data")
        != ["hannah.lim@gmail.com"]
        or upstream.get("verified_reference_retained") is not True
        or [
            (review.get("review_id"), review.get("rating_values"), review.get("customer_id"))
            for review in current_reviews.get("reviews") or []
        ] != [(352, [4], None), (353, [1], None)]
        or validity.get("status") != "unverifiable"
        or validity.get("affected_configurations") != ["chromium", "moli-1.1.3"]
        or validity.get("diagnostic_answer_eligible") is not False
        or validity.get("historical_official_scores_unchanged") is not True
    ):
        raise ValueError("task 243 guest review source mechanism differs")


def _validate_task313_store_support_audit(receipt: dict[str, Any], shopping_url: str) -> None:
    """Keep a bounded source observation distinct from a site-wide absence claim."""
    pages = receipt.get("cms_pages") or []
    expected_pages = {
        1: "no-route",
        2: "home",
        3: "enable-cookies",
        4: "privacy-policy-cookie-restriction-mode",
    }
    contact = receipt.get("contact") or {}
    if (
        receipt.get("schema") != "task313_store_support_source_audit/1"
        or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00",
            receipt.get("observed_at_utc", ""),
        )
        or receipt.get("store_phone_config") is not None
        or receipt.get("cms_block_count") != 0
        or len(pages) != len(expected_pages)
        or {page.get("id"): page.get("identifier") for page in pages} != expected_pages
        or any(
            page.get("phone_number_matches") != 0
            or not SHA256.fullmatch(str(page.get("content_sha256", "")))
            for page in pages
        )
        or contact.get("url") != shopping_url.rstrip("/") + "/contact/"
        or contact.get("http_status") != 200
        or contact.get("visible_chars", 0) <= 0
        or contact.get("tel_link_count") != 0
        or contact.get("phone_number_matches") != 0
        or not SHA256.fullmatch(str(contact.get("html_sha256", "")))
        or not SHA256.fullmatch(str(contact.get("visible_text_sha256", "")))
        or "does not cover vendor product descriptions" not in receipt.get("scope", "")
    ):
        raise ValueError("task 313 store-support source audit differs")


def _validate_task313_catalog_counterexample(receipt: dict[str, Any]) -> None:
    """A third-party product phone must not be misidentified as the store's own phone."""
    if (
        receipt.get("schema") != "task313_catalog_third_party_phone_counterexample/1"
        or receipt.get("index") != "magento2_product_1_v4"
        or receipt.get("index_document_count", 0) < 100_000
        or receipt.get("document_id") != "97396"
        or not SHA256.fullmatch(str(receipt.get("description_sha256", "")))
        or "Amazon Customer Service" not in receipt.get("quoted_context", "")
        or "1 (888)280-4331" not in receipt.get("quoted_context", "")
        or "not the One Stop Market store phone" not in receipt.get("interpretation", "")
    ):
        raise ValueError("task 313 catalog counterexample differs")


def _validate_task313_absence_audit(
    audit: dict[str, Any], source_sha: str, catalog_sha: str
) -> None:
    observation = audit.get("browser_observation") or {}
    source = audit.get("independent_source_scope") or {}
    if (
        audit.get("schema") != "trajectory_replay_task313_absence_audit/1"
        or audit.get("task_id") != 313
        or audit.get("frozen_expected_status") != "NOT_FOUND_ERROR"
        or audit.get("independent_support_source_audit_sha256") != source_sha
        or audit.get("third_party_product_phone_counterexample_sha256") != catalog_sha
        or audit.get("browser_observed_support_paths") != [
            "/", "/contact/", "/privacy-policy-cookie-restriction-mode", "/enable-cookies"
        ]
        or observation != {
            "all_http_200": True,
            "tel_links": 0,
            "phone_number_matches": 0,
            "output_status": "NOT_FOUND_ERROR",
            "retrieved_data": None,
        }
        or source != {
            "store_phone_configured": False,
            "cms_page_count": 4,
            "cms_pages_with_phone": 0,
            "cms_block_count": 0,
            "contact_page_with_phone": False,
        }
        or "不证明全站" not in audit.get("scope_limit_zh", "")
        or "does not claim" not in audit.get("scope_limit_en", "")
    ):
        raise ValueError("task 313 task-scoped absence audit differs")


def _validate_task313_current_observation(
    row: dict[str, Any],
    trace: list[dict[str, Any]],
    frozen_calls: list[dict[str, Any]],
    remote_sites: dict[WebArenaSite, str],
) -> None:
    """Accept a bounded live negative observation, not a fabricated empty answer."""
    receipt = _bound_json(row["receipt"])[1]
    physical = _bound_json(row["evidence"]["receipt.json"])[1]
    acceptance = _bound_json(row["evidence"]["retrieval-acceptance.json"])[1]
    actual = _bound_json(row["evidence"]["actual-response.json"])[1]
    candidate_path = Path(__file__).resolve().parents[2] / (
        "evaluation/trajectory-replay/webarena-verified/retrieval-candidates/task-313.json"
    )
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    expected_calls = rebind_remote_calls(
        frozen_calls, remote_sites, {"192.168.5.2", "host.docker.internal"}
    )
    if (
        receipt.get("schema") != "task313_current_observed_retrieval_aggregate/2"
        or physical.get("schema") != "task313_current_observed_retrieval_diagnostic/1"
        or physical.get("status") != "diagnostic_current_retrieval_pass"
        or physical.get("promotable") is not False
        or physical.get("site_reset") is not False
        or receipt.get("official_score") != 1
        or physical.get("official_score") != 1
        or receipt.get("candidate_sha256") != _verified_digest({
            "path": str(candidate_path),
            "sha256": receipt.get("candidate_sha256"),
        })
        or candidate.get("task_id") != 313
        or candidate.get("tool_calls", [])[:8] != frozen_calls
        or len(candidate.get("tool_calls") or []) != 9
        or len(trace) != 9
        or [step.get("arguments") for step in trace[:8]] != expected_calls
        or trace[-1].get("arguments", {}).get("action") != "evaluate"
        or physical.get("completed_action_count") != 9
        or acceptance.get("status") != "supported"
        or acceptance.get("actual_response") != actual
        or actual != {
            "task_type": "RETRIEVE",
            "status": "NOT_FOUND_ERROR",
            "retrieved_data": None,
            "error_details": None,
        }
        or row.get("evaluation_mode") != "actual_trajectory_output"
    ):
        raise ValueError("task 313 current browser negative observation differs")
    revision = receipt.get("score_only_revision") or {}
    scorer_path = Path(__file__).with_name("retrieval_acceptance.py")
    if (
        _verified_digest(revision.get("predecessor"))
        != revision.get("predecessor", {}).get("sha256")
        or revision.get("provider_call_count") != 0
        or revision.get("scorer_sha256") != _verified_digest({
            "path": str(scorer_path), "sha256": revision.get("scorer_sha256")
        })
        or revision.get("old_official_score") != 1
        or revision.get("new_official_score") != 1
        or revision.get("regression_test_ids") != [
            "test_not_found_requires_observed_complete_empty_collection_and_correct_scope",
            "test_self_declared_absence_cannot_hide_missing_or_contradictory_pages",
            "test_self_declared_absence_without_coverage_is_insufficient",
        ]
    ):
        raise ValueError("task 313 score-only revision provenance differs")
    executed_contract = json.loads(json.dumps(candidate["retrieval_contract"]))
    executed_contract["canonical_calls_sha256"] = digest([step["arguments"] for step in trace])
    executed_contract["sources"][0]["action_sha256"] = digest(trace[-1]["arguments"])
    recomputed = build_actual_response(
        contract=executed_contract,
        task_id=313,
        calls=[step["arguments"] for step in trace],
        records=trace,
        run_id="task313-current-observed",
        attempt_id="moli-fixed-physical-1",
    )
    if (
        revision.get("executed_contract_sha256") != digest(executed_contract)
        or recomputed != acceptance
        or recomputed.get("actual_response") != actual
    ):
        raise ValueError("task 313 revised absence scoring differs from browser evidence")
    try:
        observation = json.loads(trace[-1]["result"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("task 313 current observation is not JSON") from error
    pages = observation.get("pages") or []
    if (
        observation.get("scope") is not True
        or observation.get("complete") is not True
        or observation.get("items") != []
        or [page.get("path") for page in pages]
        != ["/", "/contact/", "/privacy-policy-cookie-restriction-mode", "/enable-cookies"]
        or any(
            page.get("status") != 200
            or page.get("tel") != []
            or page.get("matches") != []
            or not isinstance(page.get("text"), str)
            or len(page["text"]) < 100
            or re.search(r"(?<!\d)\+?\d[\d(). -]{7,}\d(?!\d)", page["text"])
            for page in pages
        )
    ):
        raise ValueError("task 313 support-page coverage differs")
    old = _bound_json(row["additional_evidence"][2])[1]
    if (
        old.get("schema") != "task313_remote_full_once_diagnostic/1"
        or old.get("official_score_for_synthetic_source_derived_response") != 1
        or old.get("catalog_text_complete") is not False
    ):
        raise ValueError("task 313 prior synthetic diagnostic is not disclosed")


def _validate_task163_review_source(
    source: dict[str, Any], actual: dict[str, Any]
) -> None:
    """Compare distinct low-rating titles against all public source reviews."""
    scope = source.get("source") or {}
    rows = source.get("rows") or []
    if (
        source.get("schema") != "task163_review_source_audit/1"
        or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00",
            source.get("observed_at_utc", ""),
        )
        or scope.get("app_id") != 195
        or scope.get("store_id") != 1
        or scope.get("product_id") != 38805
        or source.get("review_count") != len(rows)
        or len(rows) != 12
        or len({row.get("review_id") for row in rows}) != len(rows)
        or any(
            row.get("status_id") != 1
            or row.get("entity_pk_value") != 38805
            or row.get("vote_count") != 1
            or row.get("stars") not in {1, 2, 3, 4, 5}
            or row.get("percent") != row.get("stars") * 20
            or not isinstance(row.get("title"), str)
            or not row["title"]
            for row in rows
        )
    ):
        raise ValueError("task 163 independent review source scope differs")
    low = [row for row in rows if row["stars"] <= 2]
    titles = sorted({row["title"] for row in low})
    if (
        len(low) != 2
        or source.get("low_rating_review_count") != 2
        or titles != source.get("distinct_low_rating_titles")
        or titles != ["Memory Card Came Defective"]
        or actual.get("task_type") != "RETRIEVE"
        or actual.get("status") != "SUCCESS"
        or actual.get("retrieved_data") != titles
    ):
        raise ValueError("task 163 answer differs from independent review source")


def _validate_task163_old_action_counterexample(
    receipt: dict[str, Any],
    trace_sha: str,
    trace: list[dict[str, Any]],
    fixed_trace: list[dict[str, Any]],
) -> None:
    """Bind the old step-four timeout without inferring a source-code cause."""
    if (
        receipt.get("schema") != "remote_exact_frozen_actions_diagnostic/1"
        or receipt.get("task_id") != 163
        or receipt.get("status") != "diagnostic_incomplete"
        or receipt.get("completed_action_count") != 3
        or receipt.get("frozen_action_count") != 5
        or receipt.get("official_evaluation") != "not_run"
        or receipt.get("site_reset") is not False
        or receipt.get("promotable") is not False
        or receipt.get("trace_sha256") != trace_sha
        or receipt.get("input_sha256", {}).get("binary")
        != "3a967a1280ffe434e678b79ec24238701cbf708154dbdd490f8b9d78d03032ff"
        or len(trace) != 4
        or len(fixed_trace) != 5
        or [step.get("arguments") for step in trace]
        != [step.get("arguments") for step in fixed_trace[:4]]
        or any(step.get("tool_status") != "success" for step in trace[:3])
        or trace[3].get("index") != 4
        or trace[3].get("tool_status") != "error"
        or "review/product/listAjax/id/38805/?p=2"
        not in trace[3].get("result", "")
        or "Timeout 60000ms exceeded" not in trace[3].get("result", "")
        or fixed_trace[3].get("tool_status") != "success"
    ):
        raise ValueError("task 163 old-action counterexample differs")


def _validate_task163_visibility_probes(refs: list[dict[str, Any]]) -> None:
    """Keep contradictory same-page visibility observations in the evidence chain."""
    if len(refs) != 4:
        raise ValueError("task 163 visibility probe scope differs")
    probes = [_bound_json(ref)[1] for ref in refs]
    expected_counts = (2, 0, 0, 2)
    markup = None
    for index, (probe, expected) in enumerate(zip(probes, expected_counts, strict=True)):
        phases = {
            item.get("phase") for item in probe.get("viewport", {}).get("observations", [])
        }
        has_login_preparation = "login_before_navigation" in phases
        observations = probe.get("observations") or []
        steps = probe.get("steps") or []
        if (
            has_login_preparation != (index >= 2)
            or probe.get("viewport", {}).get("requested") != {"width": 1280, "height": 900}
            or len(steps) != 2
            or any(step.get("status") != "success" for step in steps)
            or len(observations) != 4
            or [item.get("after_seconds") for item in observations] != [0, 2, 7, 17]
            or any(
                len(item.get("matches") or []) != 4
                or item.get("playwright_visible_count") != expected
                or sum(item.get("playwright_visible") or []) != expected
                for item in observations[1:]
            )
        ):
            raise ValueError("task 163 visibility probe result differs")
        current_markup = [item.get("html") for item in observations[1]["matches"]]
        if markup is None:
            markup = current_markup
        elif current_markup != markup:
            raise ValueError("task 163 visibility probe DOM differs")
    if any("review/product/listAjax/id/38805/?p=2" not in item for item in markup or []):
        raise ValueError("task 163 visibility probe target differs")


def _order_money_cents(raw: Any) -> int:
    try:
        cents = Decimal(str(raw)) * 100
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("independent order amount is invalid") from error
    if cents != cents.to_integral_value() or cents < 0:
        raise ValueError("independent order amount is not nonnegative cents")
    return int(cents)


def _validate_task196_204_orders_source(
    task_id: int, source: dict[str, Any], trace: list[dict[str, Any]], actual: dict[str, Any]
) -> None:
    """Cross-check browser JSON answers against the separate admin order database."""
    if (
        task_id not in {196, 204}
        or source.get("schema") != "task196_204_orders_independent_source/1"
        or source.get("source", {}).get("app_id") != 195
        or source.get("source", {}).get("service_id") != "47ef97769c9644039161acb043d911c1"
        or source.get("timezone") != "America/New_York"
        or source.get("database_total") != 308
        or not re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00",
            source.get("observed_at_utc", ""),
        )
        or not trace
        or trace[-1].get("tool_status") != "success"
        or trace[-1].get("arguments", {}).get("action") != "evaluate"
        or actual.get("task_type") != "RETRIEVE"
        or actual.get("status") != "SUCCESS"
    ):
        raise ValueError(f"task {task_id} independent order source scope differs")
    try:
        observed = json.loads(trace[-1]["result"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"task {task_id} browser order observation is invalid") from error
    if (
        observed.get("scope") is not True
        or observed.get("complete") is not True
        or observed.get("total") != 308
        or observed.get("pages") != [
            {"page": 1, "rows": 200, "total": 308},
            {"page": 2, "rows": 108, "total": 308},
        ]
        or observed.get("answer") != actual.get("retrieved_data")
    ):
        raise ValueError(f"task {task_id} browser order scope differs")
    local_tz = ZoneInfo("America/New_York")
    ranked: dict[str, list[dict[str, Any]]] = {}
    for status in ("canceled", "complete"):
        rows = source.get(status) or []
        if len(rows) != 5 or len({row.get("entity_id") for row in rows}) != 5:
            raise ValueError(f"task {task_id} independent order rank boundary differs")
        normalized = []
        for row in rows:
            try:
                utc = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=UTC
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"task {task_id} independent order date differs") from error
            if row.get("status") != status or row.get("order_currency_code") != "USD":
                raise ValueError(f"task {task_id} independent order status differs")
            normalized.append({
                "entity_id": str(row["entity_id"]),
                "created_at": utc.astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S"),
                "status": status,
                "currency": "USD",
                "cents": _order_money_cents(row["grand_total"]),
            })
        if any(rows[index]["created_at"] < rows[index + 1]["created_at"]
               for index in range(4)):
            raise ValueError(f"task {task_id} independent order sorting differs")
        ranked[status] = normalized
    if task_id == 196:
        selected = observed.get("selected") or {}
        if any(selected.get(status) != ranked[status][:4] for status in ranked):
            raise ValueError("task 196 selected orders differ from independent database")
        canceled = sum(row["cents"] for row in ranked["canceled"][:4])
        complete = sum(row["cents"] for row in ranked["complete"][:4])
        if (
            observed.get("canceled_cents") != canceled
            or observed.get("complete_cents") != complete
            or actual.get("retrieved_data") != [(canceled - complete) / 100]
        ):
            raise ValueError("task 196 payment difference differs from database")
        return
    selected = observed.get("selected") or {}
    latest = source["complete"][0]
    if (
        selected != {
            "entity_id": ranked["complete"][0]["entity_id"],
            "increment_id": latest["increment_id"],
            "created_at": ranked["complete"][0]["created_at"],
            "status": "complete",
        }
        or selected["entity_id"] != "230"
    ):
        raise ValueError("task 204 latest completed order differs from database")
    items = source.get("latest_complete_order_items") or []
    parents = [item for item in items if item.get("parent_item_id") is None]
    children = [item for item in items if item.get("parent_item_id") is not None]
    if (
        len(items) != 4 or len(parents) != 2 or len(children) != 2
        or {item.get("parent_item_id") for item in children}
        != {item.get("item_id") for item in parents}
        or any(item.get("order_id") != 230 for item in items)
        or any(_order_money_cents(item.get("price")) != 0 for item in children)
        or sum(_order_money_cents(item["row_total"]) for item in parents)
        != _order_money_cents(latest["subtotal"])
    ):
        raise ValueError("task 204 independent order items differ")
    detail = observed.get("detail") or {}
    expected_items = [
        {
            "id": f"order_item_{item['item_id']}",
            "name": item["name"],
            "qty": int(Decimal(str(item["qty_ordered"]))),
            "unit": _order_money_cents(item["price"]),
            "original": _order_money_cents(item["original_price"]),
            "subtotal": _order_money_cents(item["row_total"]),
            "tax": _order_money_cents(item["tax_amount"]),
            "discount": _order_money_cents(item["discount_amount"]),
            "rowtotal": _order_money_cents(item["row_total"]),
        }
        for item in parents
    ]
    answer = [
        {"name": item["name"], "price": item["unit"] / 100}
        for item in sorted(expected_items, key=lambda item: (item["unit"], item["id"]))
    ]
    if (
        detail.get("entity_id") != selected["entity_id"]
        or detail.get("increment_id") != selected["increment_id"]
        or detail.get("body_count") != 2
        or detail.get("items") != expected_items
        or actual.get("retrieved_data") != answer
    ):
        raise ValueError("task 204 product prices differ from independent database")


def _validate_task196_204_old_actions(
    task_id: int,
    receipt: dict[str, Any],
    trace_sha: str,
    trace: list[dict[str, Any]],
    fixed_trace: list[dict[str, Any]],
) -> None:
    count = {196: 9, 204: 7}[task_id]
    if (
        receipt.get("schema") != "remote_exact_frozen_actions_diagnostic/1"
        or receipt.get("task_id") != task_id
        or receipt.get("status") != "exact_actions_complete"
        or receipt.get("completed_action_count") != count
        or receipt.get("frozen_action_count") != count
        or receipt.get("official_evaluation") != "not_run"
        or receipt.get("site_reset") is not False
        or receipt.get("promotable") is not False
        or receipt.get("trace_sha256") != trace_sha
        or receipt.get("input_sha256", {}).get("binary")
        != "3a967a1280ffe434e678b79ec24238701cbf708154dbdd490f8b9d78d03032ff"
        or len(trace) != count
        or len(fixed_trace) != count
        or [step.get("arguments") for step in trace]
        != [step.get("arguments") for step in fixed_trace]
        or any(step.get("tool_status") != "success" for step in trace)
    ):
        raise ValueError(f"task {task_id} old exact actions differ")


def _validate_task108_date_commit_evidence(
    refs: list[dict[str, Any]], original_trace: list[dict[str, Any]]
) -> None:
    """Bind the real blur transition to independently counted store-local orders."""
    if len(refs) != 6 or len(original_trace) != 14:
        raise ValueError("task 108 date-commit evidence scope differs")
    receipt_sha, receipt = _bound_json(refs[0])
    before_sha, before = _bound_json(refs[1])
    after_sha, after = _bound_json(refs[2])
    _, source = _bound_json(refs[3])
    _, old_receipt = _bound_json(refs[4])
    old_trace_sha, old_trace = _bound_json(refs[5])
    before_ref = receipt.get("evidence", {}).get("ko-before-tab.json") or {}
    after_ref = receipt.get("evidence", {}).get("ko-after-tab.json") or {}
    if (
        not SHA256.fullmatch(receipt_sha)
        or receipt.get("schema") != "task108_tab_qualification_receipt/1"
        or receipt.get("task_id") != 108
        or receipt.get("engine") != "moli"
        or receipt.get("browser_sha256") != LATEST_BINARY_SHA256
        or receipt.get("status") != "complete"
        or receipt.get("retrieval_status") != "supported"
        or receipt.get("official_score") != 1.0
        or receipt.get("promotable") is not False
        or receipt.get("completed_action_count") != 15
        or before_ref.get("sha256") != before_sha
        or after_ref.get("sha256") != after_sha
        or before.get("input", {}).get("value") != "05/31/2023"
        or before.get("input", {}).get("active") is not True
        or before.get("child", {}).get("shiftedValue") != ""
        or before.get("filters", {}).get("draft", {}).get("created_at", {}).get("to") != ""
        or after.get("input", {}).get("active") is not False
        or after.get("child", {}).get("shiftedValue") != "5/31/2023"
        or after.get("filters", {}).get("draft", {}).get("created_at", {}).get("to")
        != "5/31/2023"
        or original_trace[-1].get("tool_status") != "success"
        or re.search(
            r"Purchase Date:\s*1/01/2023\s*-\s*\.\.\.",
            original_trace[-1].get("result", ""),
        ) is None
        or old_receipt.get("schema") != "remote_exact_frozen_actions_diagnostic/1"
        or old_receipt.get("task_id") != 108
        or old_receipt.get("status") != "exact_actions_complete"
        or old_receipt.get("site_reset") is not False
        or old_receipt.get("official_evaluation") != "not_run"
        or old_receipt.get("frozen_action_count") != 14
        or old_receipt.get("completed_action_count") != 14
        or old_receipt.get("input_sha256", {}).get("binary")
        != "3a967a1280ffe434e678b79ec24238701cbf708154dbdd490f8b9d78d03032ff"
        or old_receipt.get("trace_sha256") != old_trace_sha
        or len(old_trace) != 14
        or any(step.get("tool_status") != "success" for step in old_trace)
        or [step.get("arguments") for step in old_trace]
        != [step.get("arguments") for step in original_trace]
        or re.search(
            r"Purchase Date:\s*0?1/01/2023\s*-\s*5/31/2023",
            old_trace[-1].get("result", ""),
        ) is None
    ):
        raise ValueError("task 108 browser date-commit transition differs")
    actual_ref = receipt.get("evidence", {}).get("actual_response.json")
    if not actual_ref:
        raise ValueError("task 108 browser answer is missing")
    _, actual = _bound_json(actual_ref)
    expected_counts = {
        "2023-01": 12, "2023-02": 7, "2023-03": 5, "2023-04": 9, "2023-05": 5,
    }
    expected_answer = [
        {"month": month, "count": expected_counts[f"2023-{index:02d}"]}
        for index, month in enumerate(
            ("January", "February", "March", "April", "May"), 1
        )
    ]
    if (
        actual.get("task_type") != "RETRIEVE"
        or actual.get("status") != "SUCCESS"
        or actual.get("retrieved_data") != expected_answer
        or source.get("schema") != "task108_monthly_orders_independent_source/2"
        or source.get("store_timezone") != "America/New_York"
        or source.get("month_counts") != expected_counts
        or source.get("included_count") != 38
    ):
        raise ValueError("task 108 answer differs from independent order source")
    rows = source.get("included_rows") or []
    if len(rows) != 38 or len({row.get("entity_id") for row in rows}) != 38:
        raise ValueError("task 108 independent order row scope differs")
    observed_counts = dict.fromkeys(expected_counts, 0)
    store_tz = ZoneInfo("America/New_York")
    for row in rows:
        try:
            utc = datetime.strptime(row["utc"], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=UTC
            )
            local = utc.astimezone(store_tz)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("task 108 independent order timestamp differs") from error
        month = local.strftime("%Y-%m")
        if row.get("local") != local.isoformat() or month not in observed_counts:
            raise ValueError("task 108 independent order timezone scope differs")
        observed_counts[month] += 1
    if observed_counts != expected_counts:
        raise ValueError("task 108 independent monthly counts differ")


def _verified_digest(ref: dict[str, Any]) -> str:
    path = Path(ref["path"])
    expected = ref["sha256"]
    if not path.is_absolute() or not SHA256.fullmatch(str(expected)):
        raise ValueError("diagnostic evidence reference is not an absolute SHA-256 binding")
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError("diagnostic evidence is missing or symlinked")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"diagnostic evidence hash mismatch: {path.name}")
    return actual


def load_diagnostic_closure(path: str | Path, *, old_failed_tasks: set[int]) -> dict[str, Any]:
    """Verify selected evidence and expose only compact, publication-safe facts."""
    source = Path(path)
    if (
        not source.is_absolute()
        or source.is_symlink()
        or any(parent.is_symlink() for parent in source.parents)
        or not source.is_file()
    ):
        raise ValueError("diagnostic closure index is missing or symlinked")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    index = json.loads(source.read_text(encoding="utf-8"))
    if index.get("schema") == "original36_formal_cohort_index/1":
        return _load_formal36_index(index, source_sha, old_failed_tasks=old_failed_tasks)
    if index.get("schema") == "original36_latest_moli_diagnostic_index/7":
        return _load_fixed36_index(index, source_sha, old_failed_tasks=old_failed_tasks)
    if index.get("schema") == "original36_latest_moli_diagnostic_index/6":
        return _load_latest_index(index, source_sha, old_failed_tasks=old_failed_tasks)
    if index.get("schema") != "original36_diagnostic_closure_index/5":
        raise ValueError("unsupported diagnostic closure index")
    if any(
        index.get(key) is not False
        for key in ("promotable", "formal_release", "cohort_certified", "official_scores_modified")
    ):
        raise ValueError("diagnostic index cannot alter official scores or claim certification")
    if index.get("original_task_count") != 36 or index.get("diagnostically_closed_count") != 36:
        raise ValueError("diagnostic index must cover the original 36 failures")
    if (
        index.get("inherited_old_binary_closed_count") != 27
        or set(index.get("current_b96_diagnostically_closed_task_ids", [])) != CURRENT_TASKS
    ):
        raise ValueError("diagnostic closure version groups changed")
    if (
        len(index["tasks"]) != 36
        or {int(row["task_id"]) for row in index["tasks"]} != old_failed_tasks
    ):
        raise ValueError("diagnostic closure does not match original failed results")
    _verified_digest(index["predecessor"])
    prior = json.loads(Path(index["predecessor"]["path"]).read_text(encoding="utf-8"))
    prior_by_task = {int(row["task_id"]): row for row in prior["tasks"]}
    entries = []
    for row in index["tasks"]:
        task_id = int(row["task_id"])
        current = task_id in CURRENT_TASKS
        if bool(row.get("b96_current_task_validation")) != current:
            raise ValueError(f"diagnostic version boundary differs for task {task_id}")
        prior_row = prior_by_task.get(task_id)
        if prior_row is None or prior_row.get("original_status") != "fail":
            raise ValueError(f"prior failure absent for task {task_id}")
        bindings = []
        observations = []
        if current:
            refs = list(row.get("closure_evidence") or [])
            if task_id == 108:
                refs.extend(
                    prior_row.get("b96f_diagnostic_closure", {}).get(key)
                    for key in ("completed_pair", "independent_audit")
                )
            if not refs:
                raise ValueError(f"current diagnostic task {task_id} lacks closure evidence")
            for ref in refs:
                if ref:
                    bindings.append(_verified_digest(ref))
            if task_id != 108 and not row.get("diagnosis"):
                raise ValueError(f"current diagnostic task {task_id} lacks diagnosis")
            raw_rows = (
                prior_row.get("b96f_diagnostic_closure", {}).get("physical_results", [])
                if task_id == 108
                else row.get("raw_results", [])
            )
            if {raw.get("browser") for raw in raw_rows} != {"chromium", "moli"}:
                raise ValueError(f"current diagnostic task {task_id} lacks paired browser receipts")
            for raw in raw_rows:
                receipt = raw.get("receipt")
                if isinstance(receipt, str):
                    receipt = {"path": receipt, "sha256": raw.get("receipt_sha256")}
                receipt_sha = _verified_digest(receipt)
                observations.append(
                    {
                        "browser": raw["browser"],
                        "binary_sha256": raw["binary_sha256"],
                        "receipt_sha256": receipt_sha,
                        "status": raw.get("status") or raw.get("raw_status"),
                        "completed_actions": raw.get("completed_action_count"),
                        "action_count": raw.get("action_count"),
                        "official_rule_score": raw.get("official_score"),
                    }
                )
            if task_id != 108:
                audits = row.get("downstream_results") or []
                if {audit.get("browser") for audit in audits} != {"chromium", "moli"}:
                    raise ValueError(
                        f"current diagnostic task {task_id} lacks paired downstream audit"
                    )
                for audit in audits:
                    observation = next(
                        (item for item in observations if item["browser"] == audit["browser"]),
                        None,
                    )
                    if observation is None:
                        raise ValueError(
                            f"current diagnostic task {task_id} has an unmatched browser audit"
                        )
                    observation["downstream_accepted"] = audit.get("accepted") is True
                    observation["downstream_kind"] = audit.get("acceptance_kind")
                    observation["audit_sha256"] = _verified_digest(audit["audit"])
        else:
            history = prior_row.get("history_receipt_versions") or []
            if not history:
                raise ValueError(f"inherited diagnostic task {task_id} lacks retained receipt")
            # Bind the retained last observation; earlier calls remain in the
            # external predecessor chain and are not copied into public HTML.
            bindings.append(_verified_digest(history[-1]))
            observations.append(
                {
                    "browser": history[-1].get("browser_label"),
                    "binary_sha256": history[-1].get("binary_sha256"),
                    "receipt_sha256": bindings[-1],
                    "status": history[-1].get("status"),
                    "completed_actions": None,
                    "action_count": None,
                    "official_rule_score": history[-1].get("official_score"),
                }
            )
        entries.append(
            {
                "task_id": task_id,
                "task_type": row["task_type"],
                "generation": "current_b96" if current else "inherited_mixed_build",
                "finding": CURRENT_FINDINGS.get(task_id),
                "owning_process": (
                    row["diagnosis"]["code_or_process"]
                    if current and row.get("diagnosis")
                    else "Shared trajectory input: widget readiness, date bound, and selector"
                    if task_id == 108
                    else None
                ),
                "owning_process_zh": CURRENT_PROCESS_ZH.get(task_id),
                "evidence_sha256": bindings,
                "observations": observations,
                "original_status": "fail",
                "official_score_changed": False,
            }
        )
    return {
        "schema": "lex_replay_diagnostic_closure_public/1",
        "source_sha256": source_sha,
        "predecessor_sha256": index["predecessor"]["sha256"],
        "formal_cohort": False,
        "official_scores_changed": False,
        "original_failures": 36,
        "inherited_mixed_build": 27,
        "current_b96": 9,
        "entries": sorted(entries, key=lambda item: item["task_id"]),
    }


def _load_latest_index(
    index: dict[str, Any], source_sha: str, *, old_failed_tasks: set[int]
) -> dict[str, Any]:
    """Attach five later single-browser diagnostics without changing the v5 rows."""
    if any(
        index.get(key) is not False
        for key in ("promotable", "formal_release", "cohort_certified", "official_scores_modified")
    ):
        raise ValueError("latest diagnostics cannot claim certification or modify scores")
    if _verified_digest(index["predecessor"]) != index["predecessor"]["sha256"]:
        raise ValueError("latest diagnostic predecessor changed")
    predecessor = Path(index["predecessor"]["path"])
    if json.loads(predecessor.read_text(encoding="utf-8")).get("schema") != (
        "original36_diagnostic_closure_index/5"
    ):
        raise ValueError("latest diagnostic predecessor is not the v5 original-36 index")
    baseline = load_diagnostic_closure(predecessor, old_failed_tasks=old_failed_tasks)
    release_sha = _verified_digest(index["source_receipt"])
    release = json.loads(Path(index["source_receipt"]["path"]).read_text(encoding="utf-8"))
    binary_sha = _verified_digest(index["binary"])
    archive_sha = _verified_digest(index["archive"])
    manifest_sha = _verified_digest(index["retrieval_manifest"])
    manifest = json.loads(Path(index["retrieval_manifest"]["path"]).read_text(encoding="utf-8"))
    if (
        release.get("schema") != "moli_latest_main_release_diagnostic/1"
        or release.get("source_head") != LATEST_SOURCE_HEAD
        or release.get("binary_sha256") != LATEST_BINARY_SHA256
        or release.get("source_diff_sha256_before") != release.get("source_diff_sha256_after")
        or release.get("source_changed_during_check") is not False
        or release.get("exit_code") != 0
        or binary_sha != LATEST_BINARY_SHA256
        or manifest.get("schema") != "moli_latest_main_retrieval_four_diagnostic/1"
        or manifest.get("promotable") is not False
        or manifest.get("source_head") != LATEST_SOURCE_HEAD
        or manifest.get("moli_binary_sha256") != binary_sha
        or set(manifest.get("task_ids", [])) != LATEST_TASKS - {389}
        or manifest.get("retry") is not False
    ):
        raise ValueError("latest Moli build or retrieval manifest does not match the frozen scope")
    if not SHA256.fullmatch(release.get("source_diff_sha256_after", "")):
        raise ValueError("latest Moli source-diff binding is invalid")
    candidates = manifest.get("candidate_bindings") or []
    if {row.get("task_id") for row in candidates} != LATEST_TASKS - {389}:
        raise ValueError("latest retrieval candidate inventory differs from the four tasks")
    for candidate_ref in candidates:
        _verified_digest(candidate_ref)
        if candidate_ref["task_id"] == 163:
            candidate = json.loads(
                Path(candidate_ref["path"]).read_text(encoding="utf-8")
            )
            contract = candidate.get("retrieval_contract") or {}
            if (
                candidate.get("task_id") != 163
                or contract.get("schema") != "lex_replay_current_run_retrieval/3"
                or (contract.get("semantic_reference") or {}).get("kind")
                != "reviewed_value_set"
            ):
                raise ValueError("task 163 does not bind the declared shared v3 correction")
    task_refs = index.get("tasks") or []
    if len(task_refs) != 5 or {row.get("task_id") for row in task_refs} != LATEST_TASKS:
        raise ValueError("latest Moli diagnostics require exactly five affected tasks")
    observations = []
    for task_ref in sorted(task_refs, key=lambda row: row["task_id"]):
        task_id = task_ref["task_id"]
        receipt_sha = _verified_digest(task_ref["receipt"])
        completion_sha = _verified_digest(task_ref["completion"])
        receipt = json.loads(Path(task_ref["receipt"]["path"]).read_text(encoding="utf-8"))
        completion = json.loads(Path(task_ref["completion"]["path"]).read_text(encoding="utf-8"))
        expected_status = (
            "diagnostic_mutation_completion_pass"
            if task_id == 389 else "diagnostic_current_retrieval_pass"
        )
        if (
            receipt.get("task_id") != task_id
            or completion.get("task_id") != task_id
            or receipt.get("binary_sha256") != binary_sha
            or receipt.get("status") != expected_status
            or completion.get("status") != expected_status
            or receipt.get("official_score") != 1.0
            or completion.get("official_score") != 1.0
            or receipt.get("completed_action_count") != receipt.get("action_count")
            or receipt.get("process_status") != "pass"
            or receipt.get("recorded_response_used") is not False
        ):
            raise ValueError(f"latest Moli diagnostic receipt is inconsistent for task {task_id}")
        evidence = receipt.get("evidence") or {}
        required = {"trace.json", "network.har", "evaluation.json", "candidate-input.json"}
        if task_id == 389:
            required.add("completion-observation.json")
        else:
            required.add("retrieval-acceptance.json")
        if not required.issubset(evidence):
            raise ValueError(f"latest Moli diagnostic evidence is incomplete for task {task_id}")
        evidence_hashes = {name: _verified_digest(ref) for name, ref in evidence.items()}
        evaluation = json.loads(
            Path(evidence["evaluation.json"]["path"]).read_text(encoding="utf-8")
        )
        if evaluation.get("task_id") != task_id or evaluation.get("score") != 1.0:
            raise ValueError(f"latest Moli official rule differs for task {task_id}")
        observations.append(
            {
                "task_id": task_id,
                "browser": "Moli 1.1.7",
                "status": expected_status,
                "completed_actions": receipt["completed_action_count"],
                "action_count": receipt["action_count"],
                "official_rule_score": receipt["official_score"],
                "receipt_sha256": receipt_sha,
                "completion_sha256": completion_sha,
                "evidence_sha256": evidence_hashes,
                "recorded_response_used": False,
                "qualification": "affected_task_diagnostic_only",
                "scope_note": (
                    "predeclared_shared_v3_correction" if task_id == 163
                    else "read_only_completion_observation" if task_id == 389
                    else "current_source_retrieval"
                ),
            }
        )
    return {
        **baseline,
        "source_sha256": source_sha,
        "baseline_source_sha256": baseline["source_sha256"],
        "latest_moli": {
            "formal_cohort": False,
            "official_scores_changed": False,
            "source_head": LATEST_SOURCE_HEAD,
            "source_diff_sha256": release["source_diff_sha256_after"],
            "binary_sha256": binary_sha,
            "source_receipt_sha256": release_sha,
            "retrieval_manifest_sha256": manifest_sha,
            "archive_sha256": archive_sha,
            "observations": observations,
        },
    }


def _bound_json(ref: dict[str, Any]) -> tuple[str, Any]:
    digest = _verified_digest(ref)
    return digest, json.loads(Path(ref["path"]).read_text(encoding="utf-8"))


def _load_formal36_index(
    index: dict[str, Any], source_sha: str, *, old_failed_tasks: set[int]
) -> dict[str, Any]:
    """Bind a complete new affected-task result without rewriting the historical 90."""
    diagnostic_sha, diagnostic = _bound_json(index["diagnostic_index"])
    if diagnostic.get("schema") != "original36_latest_moli_diagnostic_index/7":
        raise ValueError("formal cohort lacks the original-36 diagnostic lineage")
    historical_sha, historical = _bound_json(index["historical_index"])
    if historical.get("schema") != "original36_diagnostic_closure_index/5":
        raise ValueError("formal cohort historical results are not bound")
    model = load_diagnostic_closure(
        index["historical_index"]["path"], old_failed_tasks=old_failed_tasks
    )
    manifest_sha, manifest = _bound_json(index["manifest"])
    receipt_sha, receipt = _bound_json(index["receipt"])
    root = Path(index["evidence_root"])
    if (
        not root.is_absolute()
        or root.is_symlink()
        or not root.is_dir()
        or any(parent.is_symlink() for parent in root.parents)
        or root / "receipt.json" != Path(index["receipt"]["path"])
    ):
        raise ValueError("formal cohort evidence root is not bound")
    ids = manifest.get("candidate_task_ids")
    rows = receipt.get("results")
    if (
        manifest.get("schema") != "original36_affected_cohort_inputs/1"
        or manifest.get("promotable") is not True
        or manifest.get("quarantined") != []
        or manifest.get("source_index_sha256") != diagnostic_sha
        or ids != receipt.get("executed_task_ids")
        or set(ids or []) != old_failed_tasks
        or len(ids or []) != 36
        or not isinstance(rows, list)
        or [row.get("task_id") for row in rows] != ids
        or receipt.get("schema") != "original36_remote_affected_cohort/1"
        or receipt.get("promotable") is not True
        or receipt.get("engine") != "moli"
        or receipt.get("moli_binary_sha256") != LATEST_BINARY_SHA256
        or receipt.get("manifest_sha256") != manifest_sha
        or receipt.get("reference_sha256_before") != receipt.get("reference_sha256_after")
        or receipt.get("site_reset_between_tasks") is not False
    ):
        raise ValueError("formal affected-task cohort is incomplete or unbound")
    suffixes = {"network_har": "network.har", "acceptance": "acceptance.json",
                "actual_response": "actual_response.json", "evaluation": "evaluation.json",
                "trace": "trace.json", "readiness": "readiness.json"}
    public_rows = []
    diagnosis_by_task = {row["task_id"]: row for row in diagnostic.get("tasks", [])}
    if set(diagnosis_by_task) != old_failed_tasks:
        raise ValueError("formal cohort has no complete reviewed diagnosis inventory")
    if diagnosis_by_task[243].get("reference_validity") != {
        "status": "unverifiable",
        "affected_configurations": ["chromium", "moli-1.1.3"],
        "diagnostic_answer_eligible": False,
        "historical_official_scores_unchanged": True,
    }:
        raise ValueError("task 243 unverified reference must remain visible")
    visuals = {}
    for task_id, diagnosis in diagnosis_by_task.items():
        items = diagnosis.get("sort_menu_visual") or (
            {"last_observed": diagnosis["public_screenshot"]}
            if diagnosis.get("public_screenshot") else {}
        )
        for lane, ref in items.items():
            if ref.get("privacy_reviewed") is not True or ref.get("kind") != "last_observed_page":
                raise ValueError(f"task {task_id} visual is not public-reviewed")
            digest = _verified_digest(ref)
            visuals[f"{task_id}-{lane}"] = {
                "task_id": task_id, "lane": lane, "sha256": digest,
                "asset": f"assets/diagnostic/formal-{task_id}-{lane}.png",
                "caption_zh": ref["caption_zh"], "caption_en": ref["caption_en"],
            }
    for row, candidate in zip(rows, manifest["candidates"], strict=True):
        task_id = row["task_id"]
        hashes = row.get("evidence_sha256")
        if (
            row.get("status") != "pass"
            or row.get("score") != 1.0
            or row.get("error")
            or row.get("completed_actions") != row.get("action_count")
            or row.get("action_count") != len(candidate.get("actions", []))
            or not isinstance(hashes, dict)
            or not {"network_har", "actual_response", "evaluation", "trace", "readiness"}.issubset(hashes)
            or not set(hashes).issubset(suffixes)
        ):
            raise ValueError(f"task {task_id} is not a complete official pass")
        for key, filename in suffixes.items():
            if key not in hashes:
                continue
            evidence = root / f"task-{task_id}" / filename
            if evidence.is_symlink() or not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest() != hashes[key]:
                raise ValueError(f"task {task_id} {key} evidence changed")
        public_rows.append({
            "task_id": task_id,
            "status": "pass",
            "official_score": 1.0,
            "completed_actions": row["completed_actions"],
            "action_count": row["action_count"],
            "duration_ms": row["duration_ms"],
            "executed_actions_sha256": row["executed_actions_sha256"],
            "evidence_sha256": hashes,
            "cause": diagnosis_by_task[task_id].get("cause"),
            "reference_validity": diagnosis_by_task[task_id].get("reference_validity"),
            "task_type": diagnosis_by_task[task_id].get("task_type"),
        })
    return {
        **model,
        "source_sha256": source_sha,
        "formal36": {
            "status": "complete",
            "pass_count": len(public_rows),
            "denominator": 36,
            "binary_sha256": LATEST_BINARY_SHA256,
            "manifest_sha256": manifest_sha,
            "receipt_sha256": receipt_sha,
            "diagnostic_index_sha256": diagnostic_sha,
            "historical_index_sha256": historical_sha,
            "reference_unchanged": True,
            "site_reset_between_tasks": False,
            "rows": public_rows,
            "visuals": visuals,
        },
    }


def _qualified_trace_count(trace: Any, task_id: int, count: int) -> tuple[int, int]:
    if not isinstance(trace, list) or count <= 0:
        raise ValueError(f"task {task_id} lacks a qualified action trace")
    numbered = {}
    for step in trace:
        if (
            isinstance(step, dict)
            and type(step.get("index")) is int
            and 1 <= step["index"] <= count
        ):
            if step["index"] in numbered:
                raise ValueError(f"task {task_id} qualified action index is duplicated")
            numbered[step["index"]] = step
    if set(numbered) != set(range(1, count + 1)):
        raise ValueError(f"task {task_id} qualified action numbering is incomplete")
    completed = sum(
        (numbered[index].get("tool_status") or numbered[index].get("status")) == "success"
        for index in range(1, count + 1)
    )
    return completed, len(trace) - count


def _audit_category_requires_binding(category: str | None) -> bool:
    # A bounded audit of one unresolved task does not change the contract for
    # other unresolved tasks; concrete reviewed categories do require binding.
    return category not in {None, "unresolved"}


def _bound_cause_audit(spec: dict[str, Any], row: dict[str, Any]) -> tuple[str, Any]:
    tail_length = len(spec["evidence_receipts"]) + 2
    evidence = row.get("additional_evidence", [])
    if len(evidence) < tail_length or evidence[-tail_length].get("path") != spec["audit"]:
        raise ValueError("cause audit lacks its declared evidence binding")
    return _bound_json(evidence[-tail_length])


def _load_fixed36_index(
    index: dict[str, Any], source_sha: str, *, old_failed_tasks: set[int]
) -> dict[str, Any]:
    """Verify one candidate binary's affected-task diagnostics, not a new score cohort."""
    if any(
        index.get(key) is not False
        for key in ("promotable", "formal_release", "cohort_certified", "official_scores_modified")
    ):
        raise ValueError("fixed-binary diagnostics cannot claim certification or alter scores")
    predecessor_sha, predecessor = _bound_json(index["predecessor"])
    if predecessor.get("schema") != "original36_latest_moli_diagnostic_index/6":
        raise ValueError("fixed-binary diagnostic predecessor is not v6")
    baseline = load_diagnostic_closure(
        index["predecessor"]["path"], old_failed_tasks=old_failed_tasks
    )
    original_sha, original = _bound_json(index["original_scope"])
    if (
        original.get("schema") != "original36_diagnostic_closure_index/5"
        or {int(row["task_id"]) for row in original.get("tasks", [])} != old_failed_tasks
        or len(original.get("tasks", [])) != 36
        or set(index.get("original_failed_task_ids", [])) != old_failed_tasks
    ):
        raise ValueError("fixed-binary diagnostic scope differs from original failures")
    binary_sha = _verified_digest(index["binary"])
    source_binding_sha, source_binding = _bound_json(index["source_binding"])
    selection_sha, selection = _bound_json(index["selection"])
    if (
        binary_sha != LATEST_BINARY_SHA256
        or index.get("moli_binary_sha256") != binary_sha
        or index.get("source_head") != LATEST_SOURCE_HEAD
        or source_binding.get("source_head") != LATEST_SOURCE_HEAD
        or source_binding.get("binary_sha256") != binary_sha
        or selection.get("binary_sha256") != binary_sha
        or len(selection.get("tasks", [])) != 36
    ):
        raise ValueError("fixed-binary identity or selection binding differs")
    rows = index.get("tasks") or []
    if len(rows) != 36 or {row.get("task_id") for row in rows} != old_failed_tasks:
        raise ValueError("fixed-binary diagnostics require every original failed task once")
    cause_audit_specs = {}
    cause_audit_values = {}
    cause_audit_categories = set()
    if index.get("cause_audits") is not None:
        _, cause_manifest = _bound_json(index["cause_audits"])
        if cause_manifest.get("schema") != "original36_cause_audits/1":
            raise ValueError("fixed-binary cause audit manifest schema differs")
        cause_audit_specs = {
            item["task_id"]: item for item in cause_manifest.get("audits", [])
        }
        if len(cause_audit_specs) != len(cause_manifest.get("audits", [])) or (
            set(cause_audit_specs) - old_failed_tasks
        ):
            raise ValueError("fixed-binary cause audit task scope differs")
        rows_by_id = {row["task_id"]: row for row in rows}
        for task_id, spec in cause_audit_specs.items():
            cause_audit_values[task_id] = _bound_cause_audit(spec, rows_by_id[task_id])
            _, audit = cause_audit_values[task_id]
            category = audit.get("reviewed_cause", {}).get("category")
            if _audit_category_requires_binding(category):
                cause_audit_categories.add(category)
    frozen_ref_sha = _verified_digest(index["frozen_trajectories"])
    site_map_sha = _verified_digest(index["remote_site_map"])
    remote_sites = load_remote_sites(Path(index["remote_site_map"]["path"]))
    local_frozen_path = (
        Path(__file__).parents[2]
        / "evaluation/trajectory-replay/webarena-verified/trajectories.json"
    )
    if frozen_ref_sha != hashlib.sha256(local_frozen_path.read_bytes()).hexdigest():
        raise ValueError("fixed-binary frozen trajectories differ from this repository")
    frozen_rows = json.loads(local_frozen_path.read_text(encoding="utf-8"))["trajectories"]
    frozen = {row["task_id"]: len(row["tool_calls"]) for row in frozen_rows}
    frozen_calls = {row["task_id"]: row["tool_calls"] for row in frozen_rows}
    observations = []
    for row in sorted(rows, key=lambda item: item["task_id"]):
        task_id = row["task_id"]
        receipt_sha, receipt = _bound_json(row["receipt"])
        evaluation_sha, evaluation = _bound_json(row["evaluation"])
        if evaluation.get("task_id") != task_id or evaluation.get("score") != 1.0:
            raise ValueError(f"task {task_id} lacks an official rule pass")
        if receipt.get("task_id") != task_id:
            raise ValueError(f"task {task_id} receipt identity differs")
        receipt_binary = receipt.get("binary_sha256") or receipt.get("moli_binary_sha256")
        if receipt_binary is not None and receipt_binary != binary_sha:
            raise ValueError(f"task {task_id} receipt uses another Moli binary")
        if row.get("official_score") != 1.0:
            raise ValueError(f"task {task_id} index official score differs")
        receipt_score = receipt.get(
            "official_score", receipt.get("official_score_for_synthetic_source_derived_response")
        )
        if receipt_score is not None and receipt_score != 1.0:
            raise ValueError(f"task {task_id} receipt official score differs")
        evaluation_mode = (
            "source_derived_synthetic"
            if receipt.get("official_score_for_synthetic_source_derived_response") == 1.0
            else "actual_trajectory_output"
        )
        if row.get("evaluation_mode") != evaluation_mode:
            raise ValueError(f"task {task_id} official evaluation input mode differs")
        count = row.get("qualified_action_count")
        completed = row.get("completed_qualified_actions")
        status = row.get("action_completion")
        if type(count) is not int or type(completed) is not int or count < 0:
            raise ValueError(f"task {task_id} has invalid action counts")
        action_sha = None
        supplemental = 0
        if status == "complete_qualified":
            action_sha, trace = _bound_json(row["action_evidence"])
            actual_completed, supplemental = _qualified_trace_count(trace, task_id, count)
            if completed != count or actual_completed != count:
                raise ValueError(f"task {task_id} qualified actions were not all successful")
        elif status == "not_replayed":
            if count != 0 or completed != 0 or row.get("action_evidence") is not None:
                raise ValueError(f"task {task_id} not-replayed status has action evidence")
        else:
            raise ValueError(f"task {task_id} unsupported action completion status")
        if (
            row.get("frozen_original_action_count") != frozen.get(task_id)
            or row.get("supplemental_trace_actions") != supplemental
            or row.get("canonical_actions_replayed") is not False
        ):
            raise ValueError(f"task {task_id} action scope differs from frozen input")
        revision = row.get("input_revision") or {}
        if any(
            not SHA256.fullmatch(value)
            for key, value in revision.items()
            if key.endswith("sha256") and isinstance(value, str)
        ):
            raise ValueError(f"task {task_id} candidate-input revision hash is invalid")
        evidence_hashes = {
            name: _verified_digest(ref) for name, ref in (row.get("evidence") or {}).items()
        }
        additional = [_verified_digest(ref) for ref in row.get("additional_evidence") or []]
        base_evidence = row.get("additional_evidence") or []
        if task_id in cause_audit_specs:
            audit_tail_length = len(cause_audit_specs[task_id]["evidence_receipts"]) + 2
            if len(base_evidence) < audit_tail_length:
                raise ValueError(f"task {task_id} cause audit evidence tail is incomplete")
            base_evidence = base_evidence[:-audit_tail_length]
        semantic = row.get("semantic_verification") or {}
        if semantic.get("status") not in {"verified", "limited", "unverified"}:
            raise ValueError(f"task {task_id} lacks semantic-verification status")
        reference_validity = row.get("reference_validity")
        if task_id == 243 and semantic["status"] != "unverified":
            raise ValueError("task 243 guest identity cannot be claimed verified")
        if task_id == 243 and reference_validity != {
            "status": "unverifiable",
            "affected_configurations": ["chromium", "moli-1.1.3"],
            "diagnostic_answer_eligible": False,
            "historical_official_scores_unchanged": True,
        }:
            raise ValueError("task 243 reference validity must cover both browsers")
        if task_id != 243 and reference_validity is not None:
            raise ValueError(f"task {task_id} has an undeclared reference exception")
        identity_source_audit = None
        if task_id == 243:
            if len(base_evidence) not in {4, 5}:
                raise ValueError("task 243 requires menu and both identity-source audits")
            raw_sha, raw = _bound_json(base_evidence[1])
            public_sha, public_audit = _bound_json(base_evidence[2])
            vote_sha, vote_audit = _bound_json(base_evidence[3])
            _validate_task243_identity_audit(raw_sha, raw, public_audit, vote_sha, vote_audit)
            identity_source_audit = {
                "review_id": public_audit["review_id"],
                "review_customer_id": None,
                "exact_name_account_count": public_audit["exact_name_account_count"],
                "matching_order_count": public_audit["matching_order_count"],
                "verified_author_account_link": False,
                "public_audit_sha256": public_sha,
                "source_audit_sha256": raw_sha,
                "rating_vote_audit_sha256": vote_sha,
            }
            if len(base_evidence) == 5:
                mechanism_sha, mechanism = _bound_json(base_evidence[4])
                _validate_task243_source_mechanism_audit(mechanism)
                identity_source_audit["source_mechanism_sha256"] = mechanism_sha
                identity_source_audit["independent_curation_url"] = mechanism[
                    "independent_task_curation"
                ]["source_url"]
                identity_source_audit["upstream_verified_dataset_url"] = mechanism[
                    "upstream_dataset_comparison"
                ]["verified_dataset_url"]
                identity_source_audit["post_hoc_reference_validity"] = "unverifiable"
                identity_source_audit["affected_configurations"] = [
                    "chromium", "moli-1.1.3"
                ]
        if task_id == 313:
            if len(base_evidence) not in {3, 4}:
                raise ValueError("task 313 requires scoped support and catalog evidence")
            support_sha, source_audit = _bound_json(base_evidence[0])
            _validate_task313_store_support_audit(
                source_audit, remote_sites[WebArenaSite.SHOPPING]
            )
            catalog_sha, catalog = _bound_json(base_evidence[1])
            _validate_task313_catalog_counterexample(catalog)
            _validate_task313_current_observation(
                row, trace, frozen_calls[313], remote_sites
            )
            if semantic["status"] == "verified":
                if len(base_evidence) != 4 or semantic.get("reason_code") != (
                    "frozen_not_found_contract_and_independent_store_support_audit"
                ):
                    raise ValueError("task 313 verified absence needs a task-scoped audit")
                _, absence_audit = _bound_json(base_evidence[3])
                _validate_task313_absence_audit(absence_audit, support_sha, catalog_sha)
            elif semantic["status"] != "limited" or len(base_evidence) != 3:
                raise ValueError("task 313 not-found scope cannot be claimed exhaustive")
        cause = row.get("cause") or {}
        if not cause.get("category") or not cause.get("code_or_process"):
            raise ValueError(f"task {task_id} lacks cause classification")
        if task_id in cause_audit_specs:
            spec = cause_audit_specs[task_id]
            audit_sha, audit = cause_audit_values[task_id]
            evidence_tail = row["additional_evidence"][-(len(spec["evidence_receipts"]) + 2):]
            if (
                audit.get("task_id") != task_id
                or audit.get("reviewed_cause", {}).get("category") is None
                or evidence_tail[0]["sha256"] != audit_sha
                or evidence_tail[1]["sha256"] != audit.get("historical_result", {}).get("trace_sha256")
                or evidence_tail[1]["path"] != spec["historical_trace"]
                or [item["path"] for item in evidence_tail[2:]] != spec["evidence_receipts"]
                or any(item["sha256"] not in json.dumps(audit) for item in evidence_tail[2:])
                or cause.get("category") != audit["reviewed_cause"]["category"]
                or cause.get("code_or_process") != audit["reviewed_cause"].get(
                    "code_or_process", audit["reviewed_cause"]["category"]
                )
                or cause.get("summary_zh") != audit["reviewed_cause"]["mechanism_zh"]
                or cause.get("summary_en") != audit["reviewed_cause"]["mechanism_en"]
                or cause.get("fix_zh") != spec["fix"]["zh"]
                or cause.get("fix_en") != spec["fix"]["en"]
                or cause.get("source_location_zh") != spec["source_location"]["zh"]
                or cause.get("source_location_en") != spec["source_location"]["en"]
            ):
                raise ValueError(f"task {task_id} cause audit binding differs")
        elif cause.get("category") in cause_audit_categories:
            raise ValueError(f"task {task_id} reviewed cause lacks bound audit")
        if cause["category"] == "moli_layout_reproduced_mechanism":
            if task_id != 66 or len(row.get("additional_evidence") or []) < 2:
                raise ValueError("layout geometry finding has no task-66 evidence pair")
            geometry_ref, historical_trace_ref = row["additional_evidence"][:2]
            geometry = json.loads(Path(geometry_ref["path"]).read_text(encoding="utf-8"))
            historical_trace = json.loads(
                Path(historical_trace_ref["path"]).read_text(encoding="utf-8")
            )
            _validate_task66_geometry_diagnostic(geometry, historical_trace)
        if cause["category"] == "moli_forum_link_layout_reproduced":
            if task_id not in _FORUM_LINK_FAILURES or len(row.get("additional_evidence") or []) < 2:
                raise ValueError("forum link finding has no historical evidence pair")
            geometry_ref, historical_trace_ref = row["additional_evidence"][:2]
            geometry = json.loads(Path(geometry_ref["path"]).read_text(encoding="utf-8"))
            historical_trace = json.loads(
                Path(historical_trace_ref["path"]).read_text(encoding="utf-8")
            )
            _validate_forum_link_geometry_diagnostic(task_id, geometry, historical_trace)
        if cause["category"] == "moli_sort_menu_layout_reproduced":
            if task_id not in _SORT_MENU_FAILURES or len(row.get("additional_evidence") or []) < 2:
                raise ValueError("sort-menu finding has no historical evidence pair")
            geometry_ref, historical_trace_ref = row["additional_evidence"][:2]
            geometry = json.loads(Path(geometry_ref["path"]).read_text(encoding="utf-8"))
            historical_trace = json.loads(
                Path(historical_trace_ref["path"]).read_text(encoding="utf-8")
            )
            _validate_sort_menu_geometry_diagnostic(task_id, geometry, historical_trace)
        if cause["category"] == "moli_gitlab_avatar_layout_reproduced":
            if (
                task_id not in _GITLAB_AVATAR_FAILURES
                or len(row.get("additional_evidence") or []) < 2
                or "moli-layout/src/builder.rs" not in cause.get("source_location_en", "")
                or "moli-layout/src/projection.rs" not in cause.get("source_location_en", "")
                or "split_inline_avatar_flex_and_title_inline_both_keep_nonempty_geometry"
                not in cause.get("source_location_en", "")
            ):
                raise ValueError("GitLab avatar finding has no historical evidence pair")
            geometry_ref, historical_trace_ref = row["additional_evidence"][:2]
            geometry = json.loads(Path(geometry_ref["path"]).read_text(encoding="utf-8"))
            historical_trace = json.loads(
                Path(historical_trace_ref["path"]).read_text(encoding="utf-8")
            )
            _validate_gitlab_avatar_geometry_diagnostic(task_id, geometry, historical_trace)
        exact_original = None
        if row.get("exact_original_action_diagnostic"):
            exact = row["exact_original_action_diagnostic"]
            exact_sha, exact_receipt = _bound_json(exact["receipt"])
            exact_trace_sha, exact_trace = _bound_json(exact["action_evidence"])
            canonical = frozen_calls[task_id]
            canonical_sha = hashlib.sha256(
                json.dumps(
                    canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            if (
                exact_receipt.get("schema") != "remote_exact_frozen_actions_diagnostic/1"
                or exact_receipt.get("task_id") != task_id
                or exact_receipt.get("status") != "exact_actions_complete"
                or exact_receipt.get("promotable") is not False
                or exact_receipt.get("official_evaluation") != "not_run"
                or exact_receipt.get("site_reset") is not False
                or exact_receipt.get("frozen_calls_sha256") != canonical_sha
                or exact_receipt.get("input_sha256", {}).get("binary") != binary_sha
                or exact_receipt.get("input_sha256", {}).get("dataset") != frozen_ref_sha
                or exact_receipt.get("input_sha256", {}).get("site_map") != site_map_sha
                or exact_receipt.get("trace_sha256") != exact_trace_sha
                or exact.get("completed_actions") != len(canonical)
                or exact.get("original_actions") != len(canonical)
                or exact.get("official_evaluation") != "not_run"
                or len(exact_trace) != len(canonical)
                or [step.get("arguments") for step in exact_trace]
                != rebind_remote_calls(
                    canonical, remote_sites, {"192.168.5.2", "host.docker.internal"}
                )
                or any(
                    step.get("index") != index or step.get("tool_status") != "success"
                    for index, step in enumerate(exact_trace, 1)
                )
            ):
                raise ValueError(f"task {task_id} exact original replay differs")
            exact_original = {
                "completed_actions": len(canonical),
                "original_actions": len(canonical),
                "official_evaluation": "not_run",
                "final_url": exact_receipt["final_url"],
                "receipt_sha256": exact_sha,
                "trace_sha256": exact_trace_sha,
            }
        if task_id == 108 and semantic["status"] == "verified":
            if exact_original is None:
                raise ValueError("task 108 date-commit audit requires exact original actions")
            _validate_task108_date_commit_evidence(
                base_evidence, exact_trace
            )
        if task_id == 163 and semantic["status"] == "verified":
            if exact_original is None or len(base_evidence) != 7:
                raise ValueError("task 163 source audit requires exact original actions")
            _, source = _bound_json(base_evidence[0])
            _, actual = _bound_json(row["evidence"]["actual-response.json"])
            _validate_task163_review_source(source, actual)
            _, old_receipt = _bound_json(base_evidence[1])
            old_trace_sha, old_trace = _bound_json(base_evidence[2])
            _validate_task163_old_action_counterexample(
                old_receipt, old_trace_sha, old_trace, exact_trace
            )
            _validate_task163_visibility_probes(base_evidence[3:])
        if task_id in {196, 204} and semantic["status"] == "verified":
            if exact_original is None or len(base_evidence) != 3:
                raise ValueError(f"task {task_id} requires independent order source")
            _, source = _bound_json(base_evidence[0])
            _, actual = _bound_json(row["evidence"]["actual-response.json"])
            _validate_task196_204_orders_source(task_id, source, trace, actual)
            _, old_receipt = _bound_json(base_evidence[1])
            old_trace_sha, old_trace = _bound_json(base_evidence[2])
            _validate_task196_204_old_actions(
                task_id, old_receipt, old_trace_sha, old_trace, exact_trace
            )
        failed_original = None
        if row.get("failed_original_action_diagnostic"):
            failed = row["failed_original_action_diagnostic"]
            failed_sha, failed_receipt = _bound_json(failed["receipt"])
            failed_trace_sha, failed_trace = _bound_json(failed["action_evidence"])
            failure_contracts = {
                173: (4, "trajectory_viewport_coupling_reproduced"),
                243: (13, "trajectory_input_and_dataset_evaluator"),
            }
            failure_contract = failure_contracts.get(task_id)
            if failure_contract is None:
                raise ValueError(f"task {task_id} has no original-action failure contract")
            failed_index, expected_category = failure_contract
            canonical = frozen_calls[task_id]
            canonical_sha = hashlib.sha256(
                json.dumps(
                    canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            expected_rebound = rebind_remote_calls(
                canonical, remote_sites, {"192.168.5.2", "host.docker.internal"}
            )
            if (
                cause["category"] != expected_category
                or failed_receipt.get("schema") != "remote_exact_frozen_actions_diagnostic/1"
                or failed_receipt.get("task_id") != task_id
                or failed_receipt.get("status") != "diagnostic_incomplete"
                or failed_receipt.get("promotable") is not False
                or failed_receipt.get("official_evaluation") != "not_run"
                or failed_receipt.get("site_reset") is not False
                or failed_receipt.get("frozen_action_count") != len(canonical)
                or failed_receipt.get("completed_action_count") != failed_index - 1
                or failed_receipt.get("frozen_calls_sha256") != canonical_sha
                or failed_receipt.get("input_sha256", {}).get("binary") != binary_sha
                or failed_receipt.get("input_sha256", {}).get("dataset") != frozen_ref_sha
                or failed_receipt.get("input_sha256", {}).get("site_map") != site_map_sha
                or failed_receipt.get("trace_sha256") != failed_trace_sha
                or failed_receipt.get("viewport", {}).get("requested")
                != {"width": 1280, "height": 900}
                or len(failed_trace) != failed_index
                or any(
                    step.get("index") != index or step.get("tool_status") != "success"
                    for index, step in enumerate(failed_trace[:-1], 1)
                )
                or failed_trace[-1].get("index") != failed_index
                or failed_trace[-1].get("tool_status") != "error"
                or [step.get("arguments") for step in failed_trace]
                != expected_rebound[:failed_index]
                or "Timeout 60000ms exceeded" not in failed_trace[-1].get("result", "")
                or failed.get("completed_actions") != failed_index - 1
                or failed.get("failed_action_index") != failed_index
                or failed.get("original_actions") != len(canonical)
                or failed.get("official_evaluation") != "not_run"
                or failed.get("failed_action_error") != failed_trace[-1].get("result")
            ):
                raise ValueError(f"task {task_id} fixed-binary original red evidence differs")
            if task_id == 173:
                if len(row.get("additional_evidence") or []) < 2:
                    raise ValueError("task 173 viewport finding lacks historical review")
                historical_trace = json.loads(
                    Path(row["additional_evidence"][0]["path"]).read_text()
                )
                review = json.loads(
                    Path(row["additional_evidence"][1]["path"]).read_text()
                )
                group = next(
                    (group for group in review.get("cause_groups", [])
                     if group.get("code") == "shared_viewport_configuration"),
                    None,
                )
                revision = next(
                    (item for item in review.get("review_revisions", [])
                     if item.get("id") == "task-173-shared-viewport-20260916"),
                    None,
                )
                if (
                    len(historical_trace) < 4
                    or historical_trace[3].get("tool_status") != "error"
                    or 'a[href=\\"/search\\"]:visible'
                    not in historical_trace[3].get("result", "")
                    or group is None
                    or not any(item.get("task_id") == 173 for item in group.get("results", []))
                    or revision is None
                    or revision.get("historical_trace_observations", {}).get("media_query")
                    != "@media (min-width: 992px)"
                ):
                    raise ValueError("task 173 historical viewport review differs")
            failed_original = {
                "completed_actions": failed_index - 1,
                "original_actions": len(canonical),
                "failed_action_index": failed_index,
                "failed_action_error": failed_trace[-1]["result"].splitlines()[0],
                "viewport": {"width": 1280, "height": 900},
                "official_evaluation": "not_run",
                "receipt_sha256": failed_sha,
                "trace_sha256": failed_trace_sha,
            }
        public_screenshot = None
        if row.get("public_screenshot"):
            screenshot = row["public_screenshot"]
            screenshot_sha = _verified_digest(screenshot)
            png = Path(screenshot["path"]).read_bytes()
            if (
                screenshot.get("privacy_reviewed") is not True
                or screenshot.get("kind") != "last_observed_page"
                or len(png) > 5_000_000
                or png[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                or not all(screenshot.get(key) for key in ("caption_zh", "caption_en"))
            ):
                raise ValueError(f"task {task_id} screenshot is not publication-qualified")
            width, height = struct.unpack(">II", png[16:24])
            if not (1 <= width <= 10000 and 1 <= height <= 10000):
                raise ValueError(f"task {task_id} screenshot dimensions are invalid")
            public_screenshot = {
                "sha256": screenshot_sha,
                "kind": "last_observed_page",
                "asset": f"assets/diagnostic/{task_id}.png",
                "caption_zh": screenshot["caption_zh"],
                "caption_en": screenshot["caption_en"],
            }
        sort_menu_visual = None
        if row.get("sort_menu_visual"):
            if task_id != 29 or cause["category"] != "moli_sort_menu_layout_reproduced":
                raise ValueError("sort-menu visual is attached to the wrong task")
            geometry = json.loads(Path(row["additional_evidence"][0]["path"]).read_text())
            sort_menu_visual = {}
            for lane, engine in (("before", "moli_e688_1_1_6"), ("after", "moli_fixed_af808")):
                screenshot = row["sort_menu_visual"][lane]
                screenshot_sha = _verified_digest(screenshot)
                png = Path(screenshot["path"]).read_bytes()
                if (
                    screenshot.get("privacy_reviewed") is not True
                    or screenshot.get("kind") != "last_observed_page"
                    or screenshot_sha
                    != geometry["results"][engine]["observations"]["29"]["screenshot_sha256"]
                    or len(png) > 5_000_000
                    or png[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                    or not all(screenshot.get(key) for key in ("caption_zh", "caption_en"))
                ):
                    raise ValueError(f"task 29 {lane} visual is not publication-qualified")
                width, height = struct.unpack(">II", png[16:24])
                if not (1 <= width <= 10000 and 1 <= height <= 10000):
                    raise ValueError(f"task 29 {lane} visual dimensions are invalid")
                sort_menu_visual[lane] = {
                    "sha256": screenshot_sha,
                    "asset": f"assets/diagnostic/29-sort-{lane}.png",
                    "caption_zh": screenshot["caption_zh"],
                    "caption_en": screenshot["caption_en"],
                }
        failed_replay = None
        revised_attempts = []
        identity_neutral_ui = None
        if task_id == 243:
            red = row.get("failed_replay") or {}
            red_receipt_sha, red_receipt = _bound_json(red["receipt"])
            red_trace_sha, red_trace = _bound_json(red["action_evidence"])
            red_total = red.get("qualified_action_count")
            red_done = red.get("completed_qualified_actions")
            if (
                red_receipt.get("task_id") != 243
                or (red_receipt.get("input_sha256") or {}).get("moli_binary") != binary_sha
                or (red_receipt.get("input_sha256") or {}).get("qualified_trajectories")
                != frozen_ref_sha
                or not SHA256.fullmatch(
                    (red_receipt.get("input_sha256") or {}).get("replay_plan", "")
                )
                or red_total != 14
                or red_done != 12
                or red.get("failed_action_index") != 13
                or not isinstance(red_trace, list)
                or len(red_trace) != 13
                or [step.get("index") for step in red_trace] != list(range(1, 14))
                or any(step.get("tool_status") != "success" for step in red_trace[:12])
                or red_trace[12].get("tool_status") != "error"
                or red.get("failed_action_error") != red_trace[12].get("result")
            ):
                raise ValueError("task 243 actual replay failure differs from bound trace")
            failed_replay = {
                "qualified_action_count": 14,
                "completed_qualified_actions": 12,
                "failed_action_index": 13,
                "failed_action_error": red_trace[12]["result"],
                "receipt_sha256": red_receipt_sha,
                "action_trace_sha256": red_trace_sha,
            }
            revision = {
                "qualified_trajectories_sha256": frozen_ref_sha,
                "replay_plan_sha256": red_receipt["input_sha256"]["replay_plan"],
            }
            for attempt in row.get("revised_attempts") or []:
                revised_receipt_sha, revised_receipt = _bound_json(attempt["receipt"])
                revised_trace_sha, revised_trace = _bound_json(attempt["action_evidence"])
                guard = attempt.get("runner_guard_error") or ""
                if (
                    revised_receipt.get("task_id") != 243
                    or (revised_receipt.get("input_sha256") or {}).get("moli_binary") != binary_sha
                    or attempt.get("qualified_action_count") != 14
                    or attempt.get("official_evaluated") is not False
                    or not isinstance(revised_trace, list)
                    or len(revised_trace) != attempt.get("completed_qualified_actions")
                    or not guard.startswith("Traceback")
                    or not guard.splitlines()[-1].startswith("ValueError:")
                ):
                    raise ValueError("task 243 revised guard attempt differs from evidence")
                revised_attempts.append(
                    {
                        "revision_kind": attempt["revision_kind"],
                        "qualified_action_count": 14,
                        "completed_qualified_actions": attempt["completed_qualified_actions"],
                        "runner_guard_error": guard.splitlines()[-1],
                        "official_evaluated": False,
                        "receipt_sha256": revised_receipt_sha,
                        "action_trace_sha256": revised_trace_sha,
                    }
                )
            if row.get("identity_neutral_ui"):
                ui = row["identity_neutral_ui"]
                ui_receipt_sha, ui_receipt = _bound_json(ui["receipt"])
                grid_sha, grid = _bound_json(ui["grid"])
                derivation = ui_receipt.get("ui_source_derivation") or {}
                matches = derivation.get("exact_name_matches") or []
                if (
                    ui_receipt.get("schema") != "task243_revised_ui_diagnostic/1"
                    or ui_receipt.get("status") != "diagnostic_complete"
                    or ui_receipt.get("engine") != "moli"
                    or ui_receipt.get("ui_contract_verdict") != "pass"
                    or ui_receipt.get("identity_verified") is not False
                    or ui_receipt.get("official_score_effect") != "none"
                    or (ui_receipt.get("input_sha256") or {}).get("moli_binary") != binary_sha
                    or len(ui_receipt.get("steps") or []) != 14
                    or any(step.get("tool_status") != "success" for step in ui_receipt["steps"])
                    or ui_receipt.get("customer_grid", {}).get("evidence_sha256") != grid_sha
                    or not (grid.get("total") == grid.get("rowCount") == 70)
                    or grid.get("activeFilters") != []
                    or grid.get("tableIndex") != 1
                    or len(matches) != 1
                    or matches[0].get("name") != "Hannah Lim"
                    or matches[0].get("email") != ui_receipt.get("revised_answer_from_ui")
                    or derivation.get("author_identity_claimed") is not False
                    or ui.get("qualified_actions") != 14
                    or ui.get("customer_rows") != 70
                    or ui.get("exact_name_matches") != 1
                    or ui.get("author_identity_claimed") is not False
                    or ui.get("official_score_effect") != "none"
                ):
                    raise ValueError("task 243 identity-neutral UI proof differs")
                identity_neutral_ui = {
                    "qualified_actions": 14,
                    "customer_rows": 70,
                    "exact_name_matches": 1,
                    "author_identity_claimed": False,
                    "official_score_effect": "none",
                    "receipt_sha256": ui_receipt_sha,
                    "grid_sha256": grid_sha,
                }
        if task_id == 465:
            if row.get("timeline_evidence_sha256") not in additional:
                raise ValueError("task 465 timeline is not bound to its audit")
            sidecar = next(
                (json.loads(Path(ref["path"]).read_text(encoding="utf-8"))
                 for ref in row.get("additional_evidence") or []
                 if ref["sha256"] == row["timeline_evidence_sha256"]),
                None,
            )
            pre = (sidecar or {}).get("gate_preflight") or {}
            physical = (sidecar or {}).get("physical_result") or {}
            if (
                pre.get("deferred_state_before") != "pending"
                or pre.get("deferred_state_after") != "resolved"
                or physical.get("wishlist_post_status") != 302
                or physical.get("official_score") != 1
                or sidecar.get("original_actions_completed") != 8
                or sidecar.get("write_action_attempts") != 1
            ):
                raise ValueError("task 465 persistent mutation audit differs")
        observations.append(
            {
                "task_id": task_id,
                "task_type": row["task_type"],
                "qualified_action_count": count,
                "completed_qualified_actions": completed,
                "frozen_original_action_count": frozen[task_id],
                "action_completion": status,
                "canonical_actions_replayed": False,
                "input_revision": revision,
                "supplemental_trace_actions": supplemental,
                "official_rule_score": 1.0,
                "evaluation_mode": evaluation_mode,
                "semantic_verification": semantic,
                "cause": cause,
                "failed_replay": failed_replay,
                "revised_attempts": revised_attempts,
                "identity_neutral_ui": identity_neutral_ui,
                "identity_source_audit": identity_source_audit,
                "reference_validity": reference_validity,
                "event_timeline": row.get("event_timeline") or [],
                "public_screenshot": public_screenshot,
                "sort_menu_visual": sort_menu_visual,
                "exact_original_action_diagnostic": exact_original,
                "failed_original_action_diagnostic": failed_original,
                "timeline_evidence_sha256": row.get("timeline_evidence_sha256"),
                "post_state_audit_sha256": (
                    row.get("timeline_evidence_sha256") if task_id == 465 else None
                ),
                "evidence_sha256": {
                    "receipt": receipt_sha,
                    "official_evaluation": evaluation_sha,
                    **({"action_trace": action_sha} if action_sha else {}),
                    **evidence_hashes,
                    **{f"auxiliary_{index + 1}": digest for index, digest in enumerate(additional)},
                },
            }
        )
    counts = {
        "complete_qualified_replays": sum(
            row["action_completion"] == "complete_qualified" for row in observations
        ),
        "official_rule_pass_tasks": sum(row["official_rule_score"] == 1.0 for row in observations),
        "source_derived_official_pass_tasks": sum(
            row["evaluation_mode"] == "source_derived_synthetic" for row in observations
        ),
        "exact_original_action_diagnostics_complete": sum(
            row["exact_original_action_diagnostic"] is not None for row in observations
        ),
        "failed_original_action_diagnostics": sum(
            row["failed_original_action_diagnostic"] is not None for row in observations
        ),
        "semantic_counts": {
            status: sum(row["semantic_verification"]["status"] == status for row in observations)
            for status in ("verified", "limited", "unverified")
        },
        "unverifiable_references": sum(
            (row["reference_validity"] or {}).get("status") == "unverifiable"
            for row in observations
        ),
    }
    declared = index.get("counts") or {}
    if (
        declared.get("original_failures") != 36
        or declared.get("official_one") != counts["official_rule_pass_tasks"]
        or declared.get("complete_qualified_replays") != counts["complete_qualified_replays"]
        or declared.get("exact_canonical_replays") != 0
        or declared.get("exact_original_action_diagnostics_complete")
        != counts["exact_original_action_diagnostics_complete"]
        or declared.get("failed_original_action_diagnostics")
        != counts["failed_original_action_diagnostics"]
        or declared.get("semantic_verified") != counts["semantic_counts"]["verified"]
        or declared.get("unverifiable_references") != counts["unverifiable_references"]
    ):
        raise ValueError("fixed-binary diagnostic summary counts differ from evidence")
    return {
        **baseline,
        "source_sha256": source_sha,
        "baseline_source_sha256": baseline["source_sha256"],
        "fixed36": {
            "formal_cohort": False,
            "official_scores_changed": False,
            "source_head": LATEST_SOURCE_HEAD,
            "binary_sha256": binary_sha,
            "predecessor_sha256": predecessor_sha,
            "original_scope_sha256": original_sha,
            "source_binding_sha256": source_binding_sha,
            "selection_sha256": selection_sha,
            "frozen_trajectories_sha256": frozen_ref_sha,
            **counts,
            "observations": observations,
        },
    }
