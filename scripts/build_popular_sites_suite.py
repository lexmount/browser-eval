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
    return {
        "id": rule["id"],
        "name": f"{rule['domain']} 首页",
        "url": rule["url"],
        "category": "popular_site",
        "expected_any": [],
        "description": rule["rule_rationale"],
        "tags": ["popular-site", "china-ranked-homepage", rule["label"]],
        "required_facts": [],
        "forbidden_any": BLOCKED_TEXT,
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

    if approval["status"] != "approved_for_execution":
        raise SystemExit("China rules are not approved for execution")
    if approval["source_rules_sha256"] != sha256(CHINA_RULES):
        raise SystemExit("Approved China-rule bytes changed after review")
    if china_rules["status"] != "draft_pending_human_review" or len(china_rules["cases"]) != 40:
        raise SystemExit("Expected the exact reviewed 40-case China rule draft")
    if china_selection["selected_count"] != 40:
        raise SystemExit("Expected the approved 40-case China selection")

    homepages = deepcopy(homepage_data["cases"])
    legacy_by_id = {case["id"]: case for case in legacy_data["cases"]}
    legacy = []
    for case_id in LEGACY_IDS:
        case = deepcopy(legacy_by_id[case_id])
        case["category"] = "popular_site"
        case["tags"] = ["popular-site", "legacy-popular-list", "content_list"]
        case.setdefault("description", "旧热门网站结构案例；保留原冻结 URL 与验收契约。")
        legacy.append(bind_legacy_evidence(case))
    china = [build_china_case(rule) for rule in china_rules["cases"]]

    combined = homepages + legacy + china
    ids = [case["id"] for case in combined]
    domains = [page_domain(case) for case in combined]
    if (len(homepages), len(legacy), len(china), len(combined)) != (52, 8, 40, 100):
        raise SystemExit("Expected 52 inherited + 8 legacy unique + 40 China-ranked cases")
    if len(ids) != len(set(ids)):
        raise SystemExit("Combined suite contains duplicate case IDs")
    if len(domains) != len(set(domains)):
        duplicates = sorted({domain for domain in domains if domains.count(domain) > 1})
        raise SystemExit(f"Combined suite contains duplicate sites: {duplicates}")

    rules_payload = {
        "schema_version": 1,
        "status": "frozen_for_execution",
        "purpose": "One scoring registry for the 100-site lexmount-global report",
        "counts": {"inherited_formal_results": 52, "new_provider_calls": 48, "total": 100},
        "components": [
            {
                "kind": "global_homepage",
                "count": 52,
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
        ],
        "approval": {"path": str(APPROVAL.relative_to(ROOT)), "sha256": sha256(APPROVAL)},
        "cases": [
            {
                "id": case["id"],
                "url": case["url"],
                "cohort": (
                    "inherited_global_homepage"
                    if index < 52
                    else "new_legacy_popular_list"
                    if index < 60
                    else "new_china_ranked_homepage"
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
        "coverage_plan": {"challenge": ["lexmount-global"]},
        "comparison": {
            "kind": "popular_sites",
            "timeout_seconds": 45,
            "concurrency": 1,
            "runs_per_case": 1,
            "publication_state": "frozen_not_run",
            "require_independent_result_review": True,
            "inherited_result_count": 52,
            "new_provider_call_count": 48,
            "source_rule_registry": str(RULES_OUTPUT.relative_to(ROOT)),
            "source_rule_registry_sha256": sha256(RULES_OUTPUT),
            "approval_record": str(APPROVAL.relative_to(ROOT)),
            "approval_record_sha256": sha256(APPROVAL),
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
        "- 总计 100 站：52 条沿用当前正式结果，48 条需要新的 `lexmount-global` 调用。",
        "- 新调用由 8 个旧热门列表案例和 40 个中国榜首页组成；Amazon、Reddit 因重复只保留首页案例。",
        "- 旧 52 条 URL、契约和原始调用不变，不重复付费调用。",
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
    print("Built frozen 100-site suite: 52 inherited results + 48 new calls")


if __name__ == "__main__":
    main()
