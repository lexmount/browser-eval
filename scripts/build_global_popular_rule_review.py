#!/usr/bin/env python3
"""Validate the popular-site rule registry and build its human review view."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "research/global-popular-per-site-rules.json"
SELECTION = ROOT / "research/global-popular-selection.json"
OUTPUT = ROOT / "research/global-popular-per-site-rules-review.md"


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def anchors(case: dict) -> str:
    return "；".join(" / ".join(group) for group in case.get("anchor_groups", []))


def structure(case: dict) -> str:
    contract = case.get("record_contract")
    if contract:
        fields = [f"至少 {contract['minimum_records']} 条"]
        if contract.get("detail_link_required") is False:
            fields.append("不要求详情链接")
        if contract.get("required_fields"):
            fields.append("字段：" + "、".join(contract["required_fields"]))
        if contract.get("hosts"):
            fields.append("主机：" + "、".join(contract["hosts"]))
        if contract.get("entry_path"):
            fields.append("路径：`" + contract["entry_path"] + "`")
        if contract.get("host_policy"):
            fields.append("策略：`" + contract["host_policy"] + "`")
        return "；".join(fields)
    if case.get("body_contract"):
        contract = case["body_contract"]
        return (
            f"最多 {contract['maximum_regions']} 区；每区 ≥{contract['minimum_floor']:.0%}；"
            f"除最多 {contract['allowed_regions_below_target']} 区外均 ≥{contract['minimum_target']:.0%}"
        )
    if case.get("required_field_groups"):
        return "字段：" + "、".join(case["required_field_groups"])
    return "分布位置：" + "、".join(case.get("section_positions", []))


def validate(selection: dict, rules: dict) -> None:
    selected = {case["id"]: case for case in selection["cases"]}
    active = {case["id"]: case for case in rules["cases"]}
    excluded = {case["id"]: case for case in rules["excluded_during_rule_review"]}
    deferred = {case["id"]: case for case in rules["deferred_special_cases"]}
    if len(selected) != len(selection["cases"]):
        raise SystemExit("Duplicate selected case IDs")
    if selection.get("selected_count") != len(selected):
        raise SystemExit("Selected count does not match the final case list")
    if set(selected) != set(active) | set(deferred) or set(active) & set(deferred) or (set(active) | set(deferred)) & set(excluded):
        raise SystemExit("Active and deferred rules must exactly partition the final selected cases")
    for identifier, case in deferred.items():
        if not case.get("reason") or not case.get("evidence"):
            raise SystemExit(f"Deferred special case lacks reason or evidence: {identifier}")
    if rules.get("execution_policy") != {
        "first_run_scope": "only cases honestly expressible by the six confirmed page models and backed by executable rules",
        "scorer_gap_is_not_a_special_page": True,
        "deferred_special_cases_in_first_run": False,
        "deferred_special_cases_in_denominator": False,
        "deferred_special_cases_require_user_review_after_first_run": True,
    }:
        raise SystemExit("Special-case execution policy changed without review")
    for identifier, case in active.items():
        if case["url"] != selected[identifier]["url"]:
            raise SystemExit(f"URL changed without a declared selection revision: {identifier}")
        groups = case.get("anchor_groups", [])
        if not groups or any(not group or any(not value.strip() for value in group) for group in groups):
            raise SystemExit(f"Missing or empty anchor group: {identifier}")
        model = case["rule_model"]
        if model in {"named_link", "external_named_link", "named_item"}:
            contract = case.get("record_contract", {})
            if contract.get("minimum_records") != 3:
                raise SystemExit(f"List rule does not require exactly the agreed minimum: {identifier}")
        if model == "named_link":
            contract = case["record_contract"]
            if not contract.get("hosts") or not contract.get("entry_path"):
                raise SystemExit(f"Named-link rule lacks hosts/path: {identifier}")
        if model == "distributed_body" and not case.get("body_contract"):
            raise SystemExit(f"Article rule lacks body contract: {identifier}")
    if rules["common"] != {
        "minimum_content_chars": 1,
        "length_gate_applicable": False,
        "all_registered_anchor_groups_required": True,
        "minimum_unique_records": 3,
        "record_values_may_change": True,
        "recursive_navigation_allowed": False,
        "forbidden_page_types": [
            "login_only", "blank", "captcha_or_security_verification",
            "access_denied", "generic_error",
        ],
    }:
        raise SystemExit("Common contract changed without review")


def main() -> None:
    selection = json.loads(SELECTION.read_text())
    rules = json.loads(RULES.read_text())
    validate(selection, rules)

    cases = rules["cases"]
    labels = Counter(case["label"] for case in cases)
    statuses = Counter(case["review_status"] for case in cases)
    lines = [
        "# 全球热门网站逐站规则审阅",
        "",
        "> 本文件由 `research/global-popular-per-site-rules.json` 机械生成。规则证据只允许来自选定 URL 首次直接加载出的页面；不点击或递归访问详情页。",
        "",
        "## 范围",
        "",
        f"- 规则复核前候选：{len(selection['cases']) + len(rules['excluded_during_rule_review'])} 个。",
        f"- 规则审阅后保留：{len(cases)} 个。",
        f"- 排除：{len(rules['excluded_during_rule_review'])} 个。",
        f"- 六类模型之外暂缓：{len(rules['deferred_special_cases'])} 个；不进入第一轮调用和分母。",
        "- 最低字符数：不启用；兼容字段固定为 `min_content_chars: 1`、`length_gate_applicable: false`。",
        "- 所有登记的稳定文字组均为必需项；不以价格、排名、数量或当前条目名称作为稳定真值。",
        "",
        "标签分布：" + "、".join(f"{name} {count}" for name, count in sorted(labels.items())) + "。",
        "",
        "## 规则审阅中发现的调整",
        "",
        "- `global-popular-42-namu-wiki` 是 Wiki 门户首页，不是连续文章正文，因此从 `article` 改为 `landing_page`。",
        "- `global-popular-27-spotify` 直接呈现播放列表条目，但没有逐条详情链接，因此使用 `named_item`，不得虚构链接。",
        "- `global-popular-40-amazon-germany` 首次加载只呈现 Cookie/广告同意层；在不交互范围内没有商品目录，因此排除。",
        "- 评分器暂不支持不等于页面不适配六类模型；只有真实页面无法被六类模型诚实表达时才列入特殊页面暂缓清单。",
        "",
        "## 待审状态汇总",
        "",
    ]
    lines.extend(f"- `{status}`：{count} 个" for status, count in sorted(statuses.items()))
    lines.extend(["", "## 排除记录", ""])
    for item in rules["excluded_during_rule_review"]:
        lines.append(f"- `{item['id']}` — {item['url']} — {item['reason']}")

    lines.extend(["", "## 六类模型之外的暂缓案例", ""])
    if not rules["deferred_special_cases"]:
        lines.append("- 当前没有。")
    else:
        for item in rules["deferred_special_cases"]:
            lines.append(f"- `{item['id']}` — {item['url']} — {item['reason']}（证据：{item['evidence']}）")

    for label in sorted(labels):
        lines.extend([
            "",
            f"## {label}",
            "",
            "| 案例 | 选定 URL | 规则模型 | 必需稳定文字组 | 结构或正文要求 | 明确不冻结 | 审阅状态 |",
            "|---|---|---|---|---|---|---|",
        ])
        for case in (item for item in cases if item["label"] == label):
            lines.append(
                "| " + " | ".join([
                    f"`{cell(case['id'])}`",
                    cell(case["url"]),
                    f"`{cell(case['rule_model'])}`",
                    cell(anchors(case)),
                    cell(structure(case)),
                    cell("、".join(case.get("volatile_exclusions", []))),
                    f"`{cell(case['review_status'])}`",
                ]) + " |"
            )
    lines.extend([
        "",
        "## 人工审阅结论栏",
        "",
        "- 审阅人：待填写",
        "- 审阅结论：待填写",
        "- 未决案例：以各行 `review_status` 为准",
        "- 允许进入供应商调用的条件：路径、稳定性、跨站搜索链接与 `named_item` 支持全部核对完成，机器规则验证通过。",
        "",
    ])
    OUTPUT.write_text("\n".join(lines))
    print(f"Validated {len(cases)} active rules and built {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
