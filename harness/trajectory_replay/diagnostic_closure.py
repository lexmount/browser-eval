"""Public-safe, hash-bound view of the original 36 replay failures.

The closure index is diagnostic evidence, never a replacement result cohort.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
CURRENT_TASKS = {108, 196, 204, 243, 313, 314, 349, 464, 784}

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
        "较早诊断检查的 Contact 和 Privacy 页面未列客服电话，但不足以证明全站不存在；当前候选的范围核验未通过，不能把“未找到”推广为合格答案。",
        "The Contact and Privacy pages checked in an earlier diagnostic listed no customer-service "
        "phone, but do not establish site-wide absence. The current candidate failed its scope "
        "check, so a not-found response cannot be promoted as a qualified answer.",
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
