#!/usr/bin/env python3
"""Freeze the approved provider-blind popular-sites rule audit into current contracts."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.stable_tasks import validate_acceptance_contract
from scripts.validate_popular_sites_rule_audit import anchors


DATASET = ROOT / "evaluation/popular-sites-dataset.json"
RULES = ROOT / "research/popular-sites-rules.json"
AUDIT = ROOT / "research/popular-sites-rules-audit-v2.json"
APPROVAL = ROOT / "research/popular-sites-rules-v2-approval.json"
APPROVED_REFERENCE = ROOT / "research/popular-sites-approved-contract-reference-2026-09-15.json"
STAGE_CONTRACT = ROOT / "research/popular-sites-8-method-stage-contract.json"
FINAL_CONTRACT = ROOT / "research/popular-sites-12-method-contract.json"
CURRENT_REPORT = ROOT / "evaluation/current-report.json"

JD_URL = "https://www.jd.com/chanpin/109113.html"
ZHIHU_URL = "https://www.zhihu.com/knowledge-plan/hot-question/hot/1000001/week"
MICHELIN_URL = "https://guide.michelin.com/jp/en/shanghai-municipality/shanghai/restaurants/all-starred"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def task_prompt(contract: dict) -> str:
    base = (
        "返回至少 3 条不同的具名本站详情入口；字段完整且链接符合登记的域名与路径规则。"
        if contract["schema"] == "named_link"
        else "直接获取页面，并保留登记的稳定页面身份或主要功能文字。"
    )
    groups = [" / ".join(anchor["alternatives"]) for anchor in contract["stable_text_anchors"]]
    if groups:
        base += "同时保留这些任务相关文字：" + "；".join(groups) + "。"
    elif contract["schema"] == "named_link":
        base += "本例没有独立的稳定文字核对依据，文字覆盖不计算。"
    if contract["schema"] == "named_link":
        base += "不固定条目名称、名次、价格、评分或热度；登录、验证与错误页不能代替目标内容。"
    else:
        base += "不要求长正文或详情链接；登录、验证与错误页不能代替目标内容。"
    return base


def bind_revision_evidence(case: dict, audit_hash: str, approval_hash: str, approved_ref_hash: str) -> None:
    contract = case["acceptance_contract"]
    relative_dir = Path("sources") / "popular-sites-rules-v2" / case["id"]
    target_dir = ROOT / "evaluation" / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    evidence_sources = [
        {"path": str(AUDIT.relative_to(ROOT)), "sha256": audit_hash},
        {"path": str(APPROVAL.relative_to(ROOT)), "sha256": approval_hash},
    ]
    if case["id"] in {"challenge-02", "challenge-04", "challenge-06"}:
        evidence_sources.append(
            {"path": str(APPROVED_REFERENCE.relative_to(ROOT)), "sha256": approved_ref_hash}
        )
    stable_path = None
    stable_hash = None
    if contract["stable_text_anchors"]:
        stable_path = relative_dir / "stable-page.txt"
        stable_target = ROOT / "evaluation" / stable_path
        stable_target.write_text(
            "\n\n".join(" / ".join(row["alternatives"]) for row in contract["stable_text_anchors"])
            + "\n",
            encoding="utf-8",
        )
        stable_hash = digest(stable_target)
    evidence_path = relative_dir / "evidence.json"
    evidence_target = ROOT / "evaluation" / evidence_path
    write_json(
        evidence_target,
        {
            "schema_version": 1,
            "case_id": case["id"],
            "url": case["url"],
            "checked_at": "2026-09-15",
            "source": "Provider-blind rule audit with fresh anonymous browser observations",
            "sources": evidence_sources,
            "stable_page_sha256": stable_hash,
            "provider_output_used": False,
            "clicks_or_recursive_navigation": False,
            "scope": "Stable page text and declared named-link structure only; dynamic members and values are not frozen.",
        },
    )
    case["ground_truth_evidence"] = [
        {
            "source": evidence_path.as_posix(),
            "supports": "规则二次审计的来源、授权和结构证据",
        }
    ]
    if stable_path:
        case["ground_truth_evidence"].append(
            {"source": stable_path.as_posix(), "supports": "独立观察到的稳定页面文字组"}
        )
    annotation = case.setdefault("source_benchmark", {}).setdefault("stable_annotation", {})
    annotation.update(
        {
            "registry": "popular-sites-rules",
            "version": 3,
            "stable_fields": ["task_related_page_text"] if stable_path else [],
            "anchor_evidence": stable_path.as_posix() if stable_path else None,
            "record_requirement": (
                "At least 3 complete unique named links; each has a meaningful title and a URL matching the declared host and full path."
                if contract["schema"] == "named_link"
                else "No structured records; every registered stable page-text group is required."
            ),
            "task_decision_scope": "structure_and_explicit_stable_content",
            "live_factual_correctness": None,
            "note": "Provider-blind v2 audit; image pixels, image metadata and document title are excluded from required body text.",
        }
    )
    if stable_hash:
        annotation["anchor_evidence_sha256"] = stable_hash
    else:
        annotation.pop("anchor_evidence_sha256", None)
    evidence_hash = digest(evidence_target)
    case["source_verification"] = {
        "state": "valid",
        "checked_at": "2026-09-15",
        "method": "Provider-blind independent browser audit; three completed observations for approved URL changes",
        "reference_sha256": evidence_hash,
        "note": "Current members and dynamic values are diagnostic only and are not frozen truth.",
    }


def main() -> None:
    dataset = json.loads(DATASET.read_text())
    rules = json.loads(RULES.read_text())
    audit = json.loads(AUDIT.read_text())
    approval = json.loads(APPROVAL.read_text())
    approved_reference = json.loads(APPROVED_REFERENCE.read_text())
    stage_contract = json.loads(STAGE_CONTRACT.read_text())
    final_contract = json.loads(FINAL_CONTRACT.read_text())
    current_report = json.loads(CURRENT_REPORT.read_text())

    if digest(DATASET) != audit["bindings"]["dataset_sha256"]:
        raise SystemExit("Current dataset no longer matches the audited predecessor")
    if digest(RULES) != audit["bindings"]["rules_sha256"]:
        raise SystemExit("Current rules no longer match the audited predecessor")
    if approval["status"] != "approved_for_rule_freeze":
        raise SystemExit("Final user approval is missing")
    if approved_reference["provider_outputs_read"] or approved_reference["provider_calls"]:
        raise SystemExit("Approved references must remain provider-free")

    corrections = {row["case_id"]: row for row in audit["corrections"]}
    if len(corrections) != 21:
        raise SystemExit("Expected exactly 21 audited score-only corrections")
    dataset_cases = {case["id"]: case for case in dataset["cases"]}
    rules_cases = {case["id"]: case for case in rules["cases"]}
    if set(dataset_cases) != set(rules_cases) or len(dataset_cases) != 100:
        raise SystemExit("Dataset and rule inventory changed")

    old_hashes = {
        "dataset_sha256": digest(DATASET),
        "rules_sha256": digest(RULES),
        "stage_contract_sha256": digest(STAGE_CONTRACT),
        "final_contract_sha256": digest(FINAL_CONTRACT),
    }
    audit_hash = digest(AUDIT)
    approval_hash = digest(APPROVAL)
    approved_ref_hash = digest(APPROVED_REFERENCE)

    changed_ids = set(corrections)
    for case_id, correction in corrections.items():
        for case in (dataset_cases[case_id], rules_cases[case_id]):
            contract = case["acceptance_contract"]
            if "stable_text_groups" in correction:
                contract["stable_text_anchors"] = anchors(
                    correction["stable_text_groups"], named_link=contract["schema"] == "named_link"
                )
            if "entry_path" in correction:
                contract["entry_path"] = correction["entry_path"]
            validate_acceptance_contract(contract)

    dataset_cases["challenge-02"]["url"] = JD_URL
    rules_cases["challenge-02"]["url"] = JD_URL
    dataset_cases["challenge-04"]["url"] = ZHIHU_URL
    rules_cases["challenge-04"]["url"] = ZHIHU_URL
    changed_ids.update({"challenge-02", "challenge-04", "challenge-06"})

    michelin_contract = {
        "version": 1,
        "schema": "named_link",
        "minimum_records": 3,
        "hosts": ["guide.michelin.com"],
        "entry_path": "/jp/en/shanghai-municipality/shanghai/restaurant/[^/?#]+",
        "stable_text_anchors": [],
    }
    validate_acceptance_contract(michelin_contract)
    for case in (dataset_cases["challenge-06"], rules_cases["challenge-06"]):
        case["url"] = MICHELIN_URL
        case["acceptance_contract"] = deepcopy(michelin_contract)
    dataset_cases["challenge-06"].update(
        {
            "name": "MICHELIN Guide · 上海星级餐厅列表",
            "description": "经用户批准替换原大众点评案例；保留餐厅名称与餐厅详情链接的 named_link 任务形式。",
        }
    )
    source = dataset_cases["challenge-06"].setdefault("source_benchmark", {})
    source["mode"] = "dynamic_list_structure"
    source["replacement_for"] = "大众点评 · 上海美食列表"
    source["replacement_reason"] = "The original public list was not anonymously reproducible within the bounded independent search."
    source["captured_task_prompt"] = "取得至少三个不同餐厅的名称和本站详情链接；不固定当前餐厅、顺序、星级或价格。"
    source["hosts"] = ["guide.michelin.com"]
    source["entry_path"] = michelin_contract["entry_path"]
    source["minimum_entries"] = 3

    for case_id in sorted(changed_ids):
        case = dataset_cases[case_id]
        case["task_prompt"] = task_prompt(case["acceptance_contract"])
        case["reference_answer"] = case["task_prompt"]
        bind_revision_evidence(case, audit_hash, approval_hash, approved_ref_hash)

    score_only_ids = sorted(changed_ids - {"challenge-02", "challenge-04", "challenge-06"})
    rerun_ids = ["challenge-02", "challenge-04", "challenge-06"]
    providers = stage_contract["providers"]
    if len(providers) != 8:
        raise SystemExit("Expected the completed eight-method provider set")
    revision = {
        "id": "popular-sites-rules-v2-2026-09-15",
        "approved_at": "2026-09-15",
        "audit": str(AUDIT.relative_to(ROOT)),
        "audit_sha256": audit_hash,
        "approval": str(APPROVAL.relative_to(ROOT)),
        "approval_sha256": approval_hash,
        "approved_reference": str(APPROVED_REFERENCE.relative_to(ROOT)),
        "approved_reference_sha256": approved_ref_hash,
        "score_only_case_ids": score_only_ids,
        "provider_rerun_case_ids": rerun_ids,
        "provider_rerun_methods": providers,
        "provider_rerun_call_count": len(rerun_ids) * len(providers),
        "deferred_methods_run": False,
        "publication_hold_required": True,
        "predecessor": old_hashes,
    }
    final_contract["rule_revision"] = revision
    write_json(FINAL_CONTRACT, final_contract)
    stage_contract["final_contract"]["sha256"] = digest(FINAL_CONTRACT)
    stage_contract["rule_revision"] = revision
    stage_contract["execution"]["revision_result_plan"] = {
        "total_results": 800,
        "unchanged_or_score_only_existing_calls": 776,
        "score_only_recomputed_results": len(score_only_ids) * len(providers),
        "new_provider_calls_required": len(rerun_ids) * len(providers),
        "new_provider_call_cases": rerun_ids,
        "new_provider_call_methods": providers,
    }
    write_json(STAGE_CONTRACT, stage_contract)

    rules["status"] = "frozen_pending_affected_reruns"
    rules["execution_contract"] = {
        "path": str(STAGE_CONTRACT.relative_to(ROOT)),
        "sha256": digest(STAGE_CONTRACT),
    }
    rules["revision"] = revision
    rules["counts"].update(
        {
            "fresh_formal_results": 24,
            "inherited_formal_results": 776,
            "score_only_recomputed_results": len(score_only_ids) * len(providers),
            "provider_results_reused": 776,
        }
    )
    write_json(RULES, rules)

    dataset["comparison"].update(
        {
            "publication_state": "frozen_pending_affected_reruns",
            "fresh_formal_result_count": 24,
            "inherited_formal_result_count": 776,
            "score_only_recompute_count": len(score_only_ids) * len(providers),
            "provider_results_reused": 776,
            "required_new_provider_calls": 24,
            "source_rule_registry_sha256": digest(RULES),
            "execution_contract_sha256": digest(STAGE_CONTRACT),
            "final_execution_contract_sha256": digest(FINAL_CONTRACT),
            "rule_revision": revision,
        }
    )
    write_json(DATASET, dataset)

    current_report.update(
        {
            "publication_state": "frozen_pending_affected_reruns",
            "fresh_formal_result_count": 24,
            "inherited_formal_result_count": 776,
            "score_only_recompute_count": len(score_only_ids) * len(providers),
            "provider_results_reused": 776,
            "required_new_provider_calls": 24,
        }
    )
    write_json(CURRENT_REPORT, current_report)
    print(
        json.dumps(
            {
                "changed_cases": len(changed_ids),
                "score_only_cases": len(score_only_ids),
                "provider_rerun_cases": rerun_ids,
                "provider_rerun_methods": providers,
                "provider_rerun_calls": 24,
                "dataset_sha256": digest(DATASET),
                "rules_sha256": digest(RULES),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
