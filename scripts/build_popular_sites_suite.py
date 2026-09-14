#!/usr/bin/env python3
# ruff: noqa: E501 -- frozen Chinese review prose and evidence notes stay whole
"""Build the frozen 100-site suite from reviewed component datasets."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from harness.web_fetch.config import load_dataset
from harness.web_fetch.stable_tasks import validate_acceptance_contract

ROOT = Path(__file__).resolve().parents[1]
HOMEPAGES = ROOT / "evaluation/global-homepages-dataset.json"
LEGACY = ROOT / "evaluation/dataset.json"
CHINA_RULES = ROOT / "research/china-homepage-rules.json"
CHINA_SELECTION = ROOT / "research/china-homepage-selection.json"
APPROVAL = ROOT / "research/china-homepage-rules-approval.json"
TWELVE_METHOD_CONTRACT = ROOT / "research/popular-sites-12-method-contract.json"
STAGE_CONTRACT = ROOT / "research/popular-sites-8-method-stage-contract.json"
RULES_OUTPUT = ROOT / "research/popular-sites-rules.json"
REVIEW_OUTPUT = ROOT / "research/popular-sites-rules-review.md"
DATASET_OUTPUT = ROOT / "evaluation/popular-sites-dataset.json"

LEGACY_IDS = (
    "challenge-01",
    "challenge-02",
    "challenge-03",
    "challenge-04",
    "challenge-05",
    "challenge-06",
    "challenge-09",
    "challenge-10-justwatch",
)

BLOCKED_TEXT = [
    "Access Denied",
    "Human Verification",
    "Verify that you are human",
    "Robot or human?",
    "Checking your browser",
    "Just a moment...",
    "Prove your humanity",
    "Not Found",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page_domain(case: dict) -> str:
    source = case.get("source_benchmark", {})
    return source.get("requested_ranked_domain") or urlparse(case["url"]).hostname.removeprefix(
        "www."
    )


def china_prompt(rule: dict) -> str:
    contract = rule["acceptance_contract"]
    anchors = "；".join(
        " / ".join(item["alternatives"]) for item in contract["stable_text_anchors"]
    )
    if contract["schema"] == "named_link":
        return (
            "直接获取这个热门域名首页，返回至少 3 条不同的具名详情入口；每条必须有非空标题和有效 URL，"
            "URL 主机与完整路径符合登记规则。同时保留这些页面级稳定文字："
            f"{anchors}。不点击、不递归，不固定当前条目、顺序、价格、排名或数量。"
        )
    return (
        f"直接获取这个热门域名首页，并保留这些稳定页面身份或主要功能文字：{anchors}。"
        "不点击、不递归，不要求长正文或三个详情链接。"
    )


def build_china_case(rule: dict) -> dict:
    contract = deepcopy(rule["acceptance_contract"])
    validate_acceptance_contract(contract)
    stable_text = (
        "\n\n".join(" / ".join(item["alternatives"]) for item in contract["stable_text_anchors"])
        + "\n"
    )
    relative = Path("sources") / rule["id"] / "stable-page.txt"
    target = ROOT / "evaluation" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stable_text, encoding="utf-8")
    stable_hash = sha256(target)
    evidence = {
        "case_id": rule["id"],
        "domain": rule["domain"],
        "url": rule["url"],
        "observed_final_url": rule["observed_final_url"],
        "checked_at": rule["evidence"]["checked_at"],
        "source": "Independent Chromium initial-page capture; no provider output or recursive navigation",
        "capture_file": rule["evidence"]["capture_file"],
        "capture_file_sha256": rule["evidence"]["capture_file_sha256"],
        "visible_text_sha256": rule["evidence"]["visible_text_sha256"],
        "stable_page_sha256": stable_hash,
        "clicks_or_recursive_navigation": False,
        "provider_output_used": False,
        "scope": "Stable page-level text and declared link structure; dynamic members and values are not frozen",
    }
    write_json(target.with_name("evidence.json"), evidence)
    prompt = china_prompt(rule)
    structural_fields = ["item_title", "detail_url"] if contract["schema"] == "named_link" else []
    forbidden_any = [
        value
        for value in BLOCKED_TEXT
        if not (rule["id"] == "china-homepage-063-sina-com-cn" and value == "Not Found")
    ]
    return {
        "id": rule["id"],
        "name": f"{rule['domain']} 首页",
        "url": rule["url"],
        "category": "popular_site",
        "expected_any": [],
        "description": rule["rule_rationale"],
        "tags": ["popular-site", "china-ranked-homepage", rule["label"]],
        "required_facts": [],
        "forbidden_any": forbidden_any,
        "min_content_chars": 1,
        "fact_coverage_threshold": 1.0,
        "ground_truth_evidence": [
            {
                "source": relative.as_posix(),
                "supports": "独立 Chromium 首次加载证据中的稳定页面文字和列表结构",
            }
        ],
        "task_prompt": prompt,
        "reference_answer": prompt,
        "source_benchmark": {
            "mode": "ranked_domain_homepage",
            "rank_metadata_used_for_scoring": False,
            "ranking_source": "Semrush Most Visited Websites in China, July 2026",
            "china_rank": rule["china_rank"],
            "requested_ranked_domain": rule["domain"],
            "observed_final_url": rule["observed_final_url"],
            "stable_annotation": {
                "registry": "popular-sites-rules",
                "version": 1,
                "content_kind": rule["label"],
                "stable_fields": ["task_related_page_text"],
                "structural_only_fields": structural_fields,
                "anchor_evidence": relative.as_posix(),
                "anchor_evidence_sha256": stable_hash,
                "record_requirement": (
                    "At least 3 complete unique named links; each has a meaningful title and a URL matching the declared host and full path."
                    if structural_fields
                    else "No structured records; every registered stable page-text group is required."
                ),
                "task_decision_scope": "structure_and_explicit_stable_content",
                "live_factual_correctness": None,
                "note": "Stable anchors are page-level and cannot be supplied by counted record titles; no recursive page was used.",
            },
        },
        "ground_truth_main_content": "",
        "content_score_applicable": False,
        "track": "challenge",
        "required_patterns": [],
        "source_verification": {
            "state": "valid",
            "checked_at": "2026-09-11",
            "method": "Independent Chromium initial-page capture with hash-bound stable evidence",
            "reference_sha256": hashlib.sha256(b"").hexdigest(),
            "note": "Stable rule evidence only; current members and dynamic values are not frozen truth.",
        },
        "task_kind": "structure",
        "acceptance_contract": contract,
        "body_requirements": {},
    }


def stable_contract(anchors: list[list[str]]) -> dict:
    return {
        "version": 1,
        "schema": "page_identity",
        "minimum_records": 0,
        "stable_text_anchors": [
            {"id": f"stable-anchor-{index}", "alternatives": alternatives}
            for index, alternatives in enumerate(anchors, 1)
        ],
    }


def write_contract_evidence(case: dict, anchors: list[list[str]], contract_hash: str) -> str:
    relative = Path("sources") / "popular-sites-12-method" / case["id"] / "stable-page.txt"
    target = ROOT / "evaluation" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n\n".join(" / ".join(group) for group in anchors) + "\n", encoding="utf-8")
    write_json(
        target.with_name("evidence.json"),
        {
            "case_id": case["id"],
            "url": case["url"],
            "checked_at": "2026-09-14",
            "source": "Two fresh anonymous Chromium initial-load observations",
            "contract_record": str(TWELVE_METHOD_CONTRACT.relative_to(ROOT)),
            "contract_record_sha256": contract_hash,
            "stable_page_sha256": sha256(target),
            "provider_output_used": False,
            "clicks_or_recursive_navigation": False,
        },
    )
    return relative.as_posix()


def bind_changed_case(case: dict, anchors: list[list[str]], contract_hash: str) -> dict:
    contract = stable_contract(anchors)
    validate_acceptance_contract(contract)
    case["acceptance_contract"] = contract
    evidence_path = write_contract_evidence(case, anchors, contract_hash)
    evidence_hash = sha256(ROOT / "evaluation" / evidence_path)
    case["ground_truth_evidence"] = [
        {
            "source": evidence_path,
            "supports": "两次独立 Chromium 首次加载观察中的稳定页面文字",
        }
    ]
    annotation = case["source_benchmark"]["stable_annotation"]
    annotation["anchor_evidence"] = evidence_path
    annotation["anchor_evidence_sha256"] = evidence_hash
    annotation["record_requirement"] = (
        "No structured records; every registered stable page-text group is required."
    )
    case["source_verification"] = {
        "state": "valid",
        "checked_at": "2026-09-14",
        "method": "Two independent anonymous Chromium initial-page observations",
        "reference_sha256": hashlib.sha256(b"").hexdigest(),
        "note": "Stable rule evidence only; current members and dynamic values are not frozen truth.",
    }
    return case


def build_reserve_case(spec: dict, contract_hash: str) -> dict:
    anchors = spec["anchors"]
    contract = stable_contract(anchors)
    validate_acceptance_contract(contract)
    prompt = (
        "直接获取这个热门域名首页，并保留这些稳定页面身份或主要功能文字："
        + "；".join(" / ".join(group) for group in anchors)
        + "。不点击、不递归，不要求长正文或三个详情链接。"
    )
    case = {
        "id": spec["id"],
        "name": f"{spec['domain']} 首页",
        "url": spec["url"],
        "category": "popular_site",
        "expected_any": [],
        "description": "按候补排名补入；两次独立匿名首次加载均呈现稳定页面身份和主要功能文字。",
        "tags": ["popular-site", "china-ranked-homepage", spec["label"]],
        "required_facts": [],
        "forbidden_any": BLOCKED_TEXT,
        "min_content_chars": 1,
        "fact_coverage_threshold": 1.0,
        "task_prompt": prompt,
        "reference_answer": prompt,
        "source_benchmark": {
            "mode": "ranked_domain_homepage",
            "rank_metadata_used_for_scoring": False,
            "ranking_source": "Semrush Most Visited Websites in China, July 2026",
            "china_rank": spec["china_rank"],
            "requested_ranked_domain": spec["domain"],
            "observed_final_url": spec["observed_final_url"],
            "stable_annotation": {
                "registry": "popular-sites-rules",
                "version": 2,
                "content_kind": spec["label"],
                "stable_fields": ["task_related_page_text"],
                "structural_only_fields": [],
                "record_requirement": "No structured records; every registered stable page-text group is required.",
                "task_decision_scope": "structure_and_explicit_stable_content",
                "live_factual_correctness": None,
                "note": "Two independent browser observations; no provider output or recursive navigation was used.",
            },
        },
        "ground_truth_main_content": "",
        "content_score_applicable": False,
        "track": "challenge",
        "required_patterns": [],
        "task_kind": "structure",
        "acceptance_contract": contract,
        "body_requirements": {},
    }
    return bind_changed_case(case, anchors, contract_hash)


def bind_legacy_evidence(case: dict) -> dict:
    """Copy old immutable anchor bytes into a current evidence-record shape."""
    annotation = case["source_benchmark"]["stable_annotation"]
    old_relative = annotation.get("anchor_evidence")
    if not old_relative:
        return case
    source = ROOT / "evaluation" / old_relative
    expected = annotation["anchor_evidence_sha256"]
    if sha256(source) != expected:
        raise ValueError(f"{case['id']}: legacy anchor bytes differ from the frozen hash")
    relative = Path("sources") / "popular-sites" / case["id"] / "stable-page.txt"
    target = ROOT / "evaluation" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    write_json(
        target.with_name("evidence.json"),
        {
            "case_id": case["id"],
            "url": case["url"],
            "checked_at": case["source_verification"]["checked_at"],
            "source": "Immutable legacy stable-anchor evidence rebound for the 100-site suite",
            "source_anchor_evidence": old_relative,
            "source_anchor_evidence_sha256": expected,
            "stable_page_sha256": expected,
            "provider_output_used": False,
        },
    )
    annotation["anchor_evidence"] = relative.as_posix()
    if case.get("ground_truth_evidence"):
        for item in case["ground_truth_evidence"]:
            if item.get("source") == old_relative:
                item["source"] = relative.as_posix()
    return case


def main() -> None:
    homepage_data = json.loads(HOMEPAGES.read_text())
    legacy_data = json.loads(LEGACY.read_text())
    china_rules = json.loads(CHINA_RULES.read_text())
    china_selection = json.loads(CHINA_SELECTION.read_text())
    approval = json.loads(APPROVAL.read_text())
    twelve_method_contract = json.loads(TWELVE_METHOD_CONTRACT.read_text())
    stage_contract = json.loads(STAGE_CONTRACT.read_text())
    contract_hash = sha256(TWELVE_METHOD_CONTRACT)
    stage_contract_hash = sha256(STAGE_CONTRACT)

    if approval["status"] != "approved_for_execution":
        raise SystemExit("China rules are not approved for execution")
    if approval["source_rules_sha256"] != sha256(CHINA_RULES):
        raise SystemExit("Approved China-rule bytes changed after review")
    if china_rules["status"] != "draft_pending_human_review" or len(china_rules["cases"]) != 40:
        raise SystemExit("Expected the exact reviewed 40-case China rule draft")
    if china_selection["selected_count"] != 40:
        raise SystemExit("Expected the approved 40-case China selection")

    final_providers = twelve_method_contract["providers"]
    providers = stage_contract["providers"]
    execution = stage_contract["execution"]
    if (
        stage_contract["final_contract"]["sha256"] != contract_hash
        or len(final_providers) != 12
        or len(set(final_providers)) != 12
        or len(providers) != 8
        or len(set(providers)) != 8
        or not set(providers) < set(final_providers)
        or set(stage_contract["deferred_providers"]) != set(final_providers) - set(providers)
        or execution["formal_result_count"] != 800
        or execution["runs_per_case"] != 1
        or execution["timeout_seconds"] != 45
        or execution["within_method_concurrency"] != 1
        or not execution["methods_run_in_parallel"]
    ):
        raise SystemExit("The authorized eight-method stage contract is incomplete")

    changes = twelve_method_contract["case_changes"]
    removed_ids = {item["case_id"] for item in changes["removed"]}
    if removed_ids != {
        "global-homepage-047-booking-com",
        "global-homepage-088-primevideo-com",
    }:
        raise SystemExit("Only Booking.com and Prime Video may be replaced")

    homepages = [
        deepcopy(case) for case in homepage_data["cases"] if case["id"] not in removed_ids
    ]
    samsung = next(
        case for case in homepages if case["id"] == "global-homepage-038-samsung-com"
    )
    samsung_change = changes["url_changes"][0]
    samsung["url"] = samsung_change["to"]
    samsung["source_benchmark"]["observed_final_url"] = samsung_change["to"]
    samsung = bind_changed_case(
        samsung,
        [[item["alternatives"][0]] for item in samsung["acceptance_contract"]["stable_text_anchors"]],
        contract_hash,
    )
    legacy_by_id = {case["id"]: case for case in legacy_data["cases"]}
    legacy = []
    for case_id in LEGACY_IDS:
        case = deepcopy(legacy_by_id[case_id])
        case["category"] = "popular_site"
        case["tags"] = ["popular-site", "legacy-popular-list", "content_list"]
        case.setdefault("description", "旧热门网站结构案例；保留原冻结 URL 与验收契约。")
        legacy.append(bind_legacy_evidence(case))
    elsevier_change = changes["semantic_alternatives"][0]
    china = []
    for rule in china_rules["cases"]:
        case = build_china_case(rule)
        if case["id"] == elsevier_change["case_id"]:
            case["url"] = elsevier_change["requested_url"]
            case["source_benchmark"]["observed_final_url"] = elsevier_change[
                "observed_redirect"
            ]
            case = bind_changed_case(case, elsevier_change["anchors"], contract_hash)
        china.append(case)
    reserves = [build_reserve_case(spec, contract_hash) for spec in changes["added"]]

    combined = homepages + legacy + china + reserves
    ids = [case["id"] for case in combined]
    domains = [page_domain(case) for case in combined]
    if (len(homepages), len(legacy), len(china), len(reserves), len(combined)) != (
        50,
        8,
        40,
        2,
        100,
    ):
        raise SystemExit("Expected 50 global + 8 legacy + 40 China-ranked + 2 reserve cases")
    if len(ids) != len(set(ids)):
        raise SystemExit("Combined suite contains duplicate case IDs")
    if len(domains) != len(set(domains)):
        duplicates = sorted({domain for domain in domains if domains.count(domain) > 1})
        raise SystemExit(f"Combined suite contains duplicate sites: {duplicates}")

    rules_payload = {
        "schema_version": 1,
        "status": "frozen_ready_for_formal_execution",
        "purpose": "One scoring registry for the interim 100-site eight-of-twelve-method report",
        "counts": {
            "cases": 100,
            "methods": 8,
            "final_methods": 12,
            "fresh_formal_results": 800,
            "final_formal_results": 1200,
            "provider_results_reused": 0,
        },
        "execution_contract": {
            "path": str(STAGE_CONTRACT.relative_to(ROOT)),
            "sha256": stage_contract_hash,
        },
        "components": [
            {
                "kind": "global_homepage",
                "count": 50,
                "path": str(HOMEPAGES.relative_to(ROOT)),
                "sha256": sha256(HOMEPAGES),
            },
            {
                "kind": "legacy_popular_list",
                "count": 8,
                "path": str(LEGACY.relative_to(ROOT)),
                "sha256": sha256(LEGACY),
                "case_ids": list(LEGACY_IDS),
            },
            {
                "kind": "china_ranked_homepage",
                "count": 40,
                "path": str(CHINA_RULES.relative_to(ROOT)),
                "sha256": sha256(CHINA_RULES),
            },
            {
                "kind": "china_ranked_reserve_replacements",
                "count": 2,
                "path": str(TWELVE_METHOD_CONTRACT.relative_to(ROOT)),
                "sha256": contract_hash,
                "case_ids": [item["id"] for item in changes["added"]],
            },
        ],
        "approval": {"path": str(APPROVAL.relative_to(ROOT)), "sha256": sha256(APPROVAL)},
        "cases": [
            {
                "id": case["id"],
                "url": case["url"],
                "cohort": (
                    "global_homepage"
                    if index < 50
                    else "legacy_popular_list"
                    if index < 58
                    else "china_ranked_homepage"
                    if index < 98
                    else "china_ranked_reserve_replacement"
                ),
                "acceptance_contract": case["acceptance_contract"],
            }
            for index, case in enumerate(combined)
        ],
    }
    write_json(RULES_OUTPUT, rules_payload)

    dataset = {
        "schema_version": 1,
        "id": "popular-sites",
        "name": "全球热门网站 100 站 Web Fetch 评测",
        "tool": "web_fetch",
        "dynamic_scoring": "按独立观察的稳定页面文字与页面实际结构验收；不冻结动态条目和值。",
        "coverage_plan": {"challenge": providers},
        "comparison": {
            "kind": "popular_sites",
            "timeout_seconds": 45,
            "concurrency": execution["within_method_concurrency"],
            "runs_per_case": 1,
            "reference_binding_mode": "hash_bound_stable_text_and_structure",
            "publication_state": "frozen_ready_for_formal_execution",
            "unverified_reference_cases": [],
            "require_independent_result_review": True,
            "fresh_formal_result_count": execution["formal_result_count"],
            "provider_results_reused": 0,
            "methods_run_in_parallel": True,
            "retry_policy": execution["retry_policy"],
            "body_reference_case_count": 0,
            "body_metrics": "unavailable",
            "conversion": "Each method's native known-URL fetch output is normalized once into text plus explicit structured links when available.",
            "source_rule_registry": str(RULES_OUTPUT.relative_to(ROOT)),
            "source_rule_registry_sha256": sha256(RULES_OUTPUT),
            "approval_record": str(APPROVAL.relative_to(ROOT)),
            "approval_record_sha256": sha256(APPROVAL),
            "execution_contract": str(STAGE_CONTRACT.relative_to(ROOT)),
            "execution_contract_sha256": stage_contract_hash,
            "final_execution_contract": str(TWELVE_METHOD_CONTRACT.relative_to(ROOT)),
            "final_execution_contract_sha256": contract_hash,
            "stage_label": "8/12 methods interim",
            "deferred_providers": stage_contract["deferred_providers"],
        },
        "cases": combined,
    }
    write_json(DATASET_OUTPUT, dataset)
    _, loaded = load_dataset(DATASET_OUTPUT)
    if len(loaded) != 100:
        raise SystemExit("Generated dataset case count changed during load")

    lines = [
        "# 全球热门网站 100 站规则冻结记录",
        "",
        "- 本阶段为 100 站 × 8/12 方法 = 800 条全新正式结果；旧 46/100 Lexmount Global 结果不复用、不覆盖。",
        "- Cloudflare、Firecrawl、Tavily、Exa 本阶段未运行，不计为失败或 0 分；最终目标仍为 12 方法 1,200 条结果。",
        "- Samsung 固定到美国站；Elsevier 允许独立观察到的同语义中英文页面文字。",
        "- Booking.com 与 Prime Video 因当前地区访问契约不可复现而移出新矩阵，由候补 Grok、Shopify 补足。",
        "- 每案例每方法 1 次、45 秒外层截止；方法内串行、方法间并行，禁止为提高分数而重试。",
        "- Crawl4AI 内部页面上限为 40 秒，等待 domcontentloaded 后稳定 1 秒并预留 5 秒清理落盘；仍受统一 45 秒外层截止。",
        "- 报告对齐参考报告的信息架构和设计系统，但如实展示 100 案例、8/12 方法和单一热门网站任务族。",
        "- 报告继续使用仓库唯一的 `harness/web_fetch/report.py` 和 `scripts/build_report.py`。",
        "- `named_link` 的稳定文字属于直接加载页面，不要求每条记录包含；每条计数记录必须独立具备有效标题和匹配主机、完整路径的 URL。",
        "",
        "## 冻结清单",
        "",
        "| 序号 | 案例 | 队列 | URL | 规则 |",
        "|---:|---|---|---|---|",
    ]
    for index, item in enumerate(rules_payload["cases"], 1):
        lines.append(
            f"| {index} | `{item['id']}` | `{item['cohort']}` | {item['url']} | `{item['acceptance_contract']['schema']}` |"
        )
    REVIEW_OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Built frozen 100-site x 8-of-12-method stage: 800 formal results")


if __name__ == "__main__":
    main()
