#!/usr/bin/env python3
"""Build the unrun representative-public-content Web Fetch suite.

This is deliberately a mechanical packaging step.  It does not promote rules
whose review status still calls for new evidence or scorer work, and it does
not refresh any page or provider output.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness.web_fetch.config import load_dataset
from harness.web_fetch.stable_tasks import validate_acceptance_contract


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "research/global-popular-per-site-rules.json"
SELECTION_PATH = ROOT / "research/global-popular-selection.json"
DATASET_PATH = ROOT / "evaluation/global-representative-content-dataset.json"
REVIEW_PATH = ROOT / "research/global-representative-content-dataset-review.md"
SOURCES_ROOT = ROOT / "evaluation/sources"

READY_STATUSES = {
    "ready",
    "ready_from_prior_chromium_evidence",
    "ready_with_declared_redirect_to_bbc_com",
    "ready_with_partial_page_error_exclusion",
    "relabel_ready_from_prior_chromium_evidence",
}
BLOCKED_PAGE_TEXT = [
    "Access denied",
    "403 Forbidden",
    "Verify that you are human",
    "Checking your browser",
    "Just a moment...",
    "Attention Required!",
    "CAPTCHA",
]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def anchor_contract(case: dict) -> dict:
    anchors = [
        {
            "id": f"stable-anchor-{index}",
            "alternatives": group,
            **({"match_scope": "non_record_lines"} if case["rule_model"] == "named_link" else {}),
        }
        for index, group in enumerate(case["anchor_groups"], 1)
    ]
    if case["rule_model"] == "named_link":
        records = case["record_contract"]
        contract = {
            "version": 1,
            "schema": "named_link",
            "minimum_records": records["minimum_records"],
            "hosts": records["hosts"],
            "entry_path": records["entry_path"],
            "stable_text_anchors": anchors,
        }
    elif case["rule_model"] in {"detail_fields", "landing_sections"}:
        contract = {
            "version": 1,
            "schema": "page_identity",
            "minimum_records": 0,
            "stable_text_anchors": anchors,
        }
    else:
        raise ValueError(f"{case['id']}: ready rule uses unsupported model {case['rule_model']}")
    validate_acceptance_contract(contract)
    return contract


def write_anchor_evidence(case: dict, rules_hash: str, selection_hash: str) -> tuple[str, str]:
    relative = Path("sources") / case["id"] / "stable-page.txt"
    target = ROOT / "evaluation" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\n\n".join(" / ".join(group) for group in case["anchor_groups"]) + "\n"
    target.write_text(text, encoding="utf-8")
    anchor_hash = sha256_bytes(target.read_bytes())
    evidence = {
        "case_id": case["id"],
        "url": case["url"],
        "checked_at": "2026-09-10",
        "source": "Previously completed direct-page browser review, mechanically packaged from the retained rule registry",
        "rule_registry": str(RULES_PATH.relative_to(ROOT)),
        "rule_registry_sha256": rules_hash,
        "selection_registry": str(SELECTION_PATH.relative_to(ROOT)),
        "selection_registry_sha256": selection_hash,
        "stable_page_sha256": anchor_hash,
        "scope": "Stable text observed on the selected URL's initial page; no recursive navigation",
        "dynamic_values_are_not_fixed_answers": True,
        "provider_output_used": False,
    }
    target.with_name("evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return relative.as_posix(), anchor_hash


def task_text(case: dict, contract: dict) -> str:
    anchors = "；".join(" / ".join(group) for group in case["anchor_groups"])
    if contract["schema"] == "named_link":
        return (
            f"直接获取给定页面，返回至少 {contract['minimum_records']} 条不同的具名详情入口，"
            f"链接须符合登记的主机和路径；同时保留这些稳定页面文字：{anchors}。"
            "不点击条目，不固定当前成员、顺序、价格、排名、数量或热度；登录、空白、验证码和错误页不能代替目标内容。"
        )
    return (
        f"直接获取给定页面并保留这些已登记的页面身份或核心功能文字：{anchors}。"
        "不要求列表记录，不点击或递归访问子页面；登录、空白、验证码和错误页不能代替目标内容。"
    )


def build_case(case: dict, selection: dict, rules_hash: str, selection_hash: str) -> dict:
    contract = anchor_contract(case)
    evidence_path, evidence_hash = write_anchor_evidence(case, rules_hash, selection_hash)
    prompt = task_text(case, contract)
    structural_fields = (
        ["item_title", "detail_url", *case.get("volatile_exclusions", [])]
        if contract["schema"] == "named_link"
        else list(case.get("volatile_exclusions", []))
    )
    return {
        "id": case["id"],
        "name": f"{selection['domain']} · {case['label']}",
        "url": case["url"],
        "category": "representative_public_content",
        "expected_any": [],
        "description": selection["reason"],
        "tags": ["global-popular", "representative-content", case["label"]],
        "required_facts": [],
        "forbidden_any": BLOCKED_PAGE_TEXT,
        "min_content_chars": 1,
        "fact_coverage_threshold": 1.0,
        "ground_truth_evidence": [
            {
                "source": evidence_path,
                "supports": "此前独立浏览器逐页审阅所保留的稳定页面文字；不包含供应商输出",
            }
        ],
        "task_prompt": prompt,
        "reference_answer": prompt,
        "source_benchmark": {
            "mode": "representative_public_content",
            "selection_role": "extension_cohort_not_ranked_domain_homepage",
            "rule_model": case["rule_model"],
            "review_status": case["review_status"],
            "stable_annotation": {
                "registry": "global-popular-per-site-rules",
                "version": 1,
                "content_kind": case["label"],
                "stable_fields": ["task_related_page_text"],
                "structural_only_fields": structural_fields,
                "anchor_evidence": evidence_path,
                "anchor_evidence_sha256": evidence_hash,
                "record_requirement": (
                    "At least 3 complete unique named links; additional legitimate records are allowed."
                    if contract["schema"] == "named_link"
                    else "No structured records; all registered stable page-text groups are required."
                ),
                "task_decision_scope": "structure_and_explicit_stable_content",
                "live_factual_correctness": None,
                "note": "Packaged from retained independent browser-review rules; this suite is delivered but not run.",
            },
        },
        "ground_truth_main_content": "",
        "content_score_applicable": False,
        "track": "challenge",
        "required_patterns": [],
        "source_verification": {
            "state": "valid",
            "checked_at": "2026-09-10",
            "method": "Retained direct-page browser review; hash-bound stable-text evidence",
            "reference_sha256": hashlib.sha256(b"").hexdigest(),
            "note": "Stable-rule evidence only; no claim of frozen list membership or full-body truth.",
        },
        "task_kind": "structure",
        "acceptance_contract": contract,
        "body_requirements": {},
    }


def build_review(cases: list[dict], excluded: list[dict]) -> None:
    lines = [
        "# 全球热门网站代表性公开内容测试集",
        "",
        "> 本文件由 `scripts/build_global_representative_dataset.py` 生成。它打包此前已经保留的逐站规则，不重新访问网页，也不使用供应商输出。该测试集不会在本轮运行。",
        "",
        "## 范围结论",
        "",
        f"- 可执行案例：{len(cases)} 个。",
        f"- 暂不纳入：{len(excluded)} 个；原因是既有审阅状态仍要求补证据、核路径或扩展评分器。",
        "- 这些 URL 是人工选择的代表性公开内容页，不是 Similarweb 榜单指定的首页，因此与新首页主测试集分开。",
        "- `named_link` 页面要求至少 3 个唯一名称＋详情链接；详情页和落地页使用 `page_identity`，要求命中全部登记的稳定文字组。",
        "- 所有稳定文字仅来自选定 URL 的首次直接加载范围；不点击、不递归、不冻结当前列表成员或动态数值。",
        "",
        "## 已打包案例",
        "",
        "| 案例 | URL | 标签 | 验收 schema | 稳定文字组数 | 原审阅状态 |",
        "|---|---|---|---|---:|---|",
    ]
    for case in cases:
        lines.append(
            f"| `{case['id']}` | {case['url']} | `{case['tags'][-1]}` | "
            f"`{case['acceptance_contract']['schema']}` | "
            f"{len(case['acceptance_contract']['stable_text_anchors'])} | "
            f"`{case['source_benchmark']['review_status']}` |"
        )
    lines.extend(["", "## 暂不纳入", ""])
    for case in excluded:
        lines.append(f"- `{case['id']}` — `{case['review_status']}` — {case['url']}")
    lines.extend([
        "",
        "## 人工核验栏",
        "",
        "- 数据集：`evaluation/global-representative-content-dataset.json`",
        "- 本轮供应商调用：未运行",
        "- 后续若要运行：先重新核对页面证据新鲜度，并单独确认这套扩展组的运行范围。",
        "",
    ])
    REVIEW_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    rules_raw = RULES_PATH.read_bytes()
    selection_raw = SELECTION_PATH.read_bytes()
    rules = json.loads(rules_raw)
    selection_payload = json.loads(selection_raw)
    selected = {case["id"]: case for case in selection_payload["cases"]}
    ready = [case for case in rules["cases"] if case["review_status"] in READY_STATUSES]
    excluded = [case for case in rules["cases"] if case["review_status"] not in READY_STATUSES]
    if len(ready) != 45 or len(excluded) != 12:
        raise SystemExit(f"Expected 45 ready and 12 unresolved rules, got {len(ready)} and {len(excluded)}")
    rules_hash = sha256_bytes(rules_raw)
    selection_hash = sha256_bytes(selection_raw)
    cases = [build_case(case, selected[case["id"]], rules_hash, selection_hash) for case in ready]
    dataset = {
        "schema_version": 1,
        "id": "global-representative-content",
        "name": "全球热门网站代表性公开内容扩展组",
        "tool": "web_fetch",
        "dynamic_scoring": "列表只验收具名详情入口结构与稳定页面文字；详情/落地页验收稳定身份与功能文字，不冻结动态值。",
        "coverage_plan": {"challenge": []},
        "comparison": {
            "kind": "representative_public_content_extension",
            "timeout_seconds": 45,
            "concurrency": 1,
            "runs_per_case": 1,
            "publication_state": "dataset_only_not_run",
            "require_independent_result_review": True,
            "source_rule_registry": str(RULES_PATH.relative_to(ROOT)),
            "source_rule_registry_sha256": rules_hash,
        },
        "cases": cases,
    }
    DATASET_PATH.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _, loaded = load_dataset(DATASET_PATH)
    if len(loaded) != 45:
        raise SystemExit("Generated dataset did not load with 45 cases")
    build_review(cases, excluded)
    print(f"Built {DATASET_PATH.relative_to(ROOT)} with {len(cases)} unrun cases")
    print(f"Built {REVIEW_PATH.relative_to(ROOT)}; {len(excluded)} unresolved rules remain excluded")


if __name__ == "__main__":
    main()
