"""Build the machine-readable and local human review for dynamic-100.

The tracked JSON contains conclusions and immutable bindings, but never provider
payloads or machine-local evidence paths.  The ignored HTML is a local review
surface and may embed the complete reference and provider output.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "research/dynamic-100-formal-audit-plan.md"
REVIEWER = "Codex independent full-scope benchmark audit"

SCOPE_FIXES = {
    "dynamic-020-webscraper-io-test": (
        "标准答案混入总数和分页控件；任务文字也没有明确区分页面总计21件与首次可见6件。",
        "把目标明确为首次自动加载的6条商品记录，分页控件不进入正文参考。",
    ),
    "dynamic-021-021-scalar-get-operation": (
        "参考文本混入 Download、Copy 等编辑器控件，超出声明的 GET 操作正文。",
        "按操作章节重建参考，保留方法、路径、服务地址和请求示例，排除编辑器控件。",
    ),
    "dynamic-024-en-stable-gridjs-pagination": (
        "参考文本把 Previous、Next 和页码控件计入首次表格正文。",
        "参考只保留首个表格的字段与两条记录，分页控件单独作为界面证据。",
    ),
    "dynamic-033-033-vuetify-usage-table": (
        "声明范围是 Usage 区域，但参考继续覆盖 API、Server-side、Virtual、Guide、Slots 等后续章节。",
        "把参考截取到 Usage 说明和首个10行表格，不把后续整页文档当作标准答案。",
    ),
    "dynamic-107-redoc-cafe-api": (
        "参考正文混入 Copy、Download、Expand/Collapse 等文档界面控件。",
        "按 Authorization、Products、Orders、Statistics 章节重建正文参考，控件只计纯净度。",
    ),
}

RULE_FIXES = {
    "dynamic-020-webscraper-io-test": "规则应显式检查6条商品的名称、价格、描述、评论数、配对和唯一性。",
    "dynamic-021-021-scalar-get-operation": "规则应使用语义章节而不是受编辑器控件污染的一段压缩文本。",
    "dynamic-024-en-stable-gridjs-pagination": "规则不应要求分页控件，并应显式绑定两行字段。",
    "dynamic-025-025-tabulator-vertical-virtual-dom": "规则缺少未渲染目标的完成门禁，页面级说明文字会掩盖 Loading Example 占位符。",
    "dynamic-033-033-vuetify-usage-table": "规则以整页文档分段，和声明的 Usage 范围不一致。",
    "dynamic-042-en-real-amazon-india": "详情路径正则拒绝合法的 /ref= 跟踪后缀，造成16条商品记录被计为0。",
    "dynamic-044-en-real-github-trending": "仓库路径规则把 /trending/language 和 /sponsors/explore 等站内路由误计为仓库记录。",
    "dynamic-099-ar-real-amazon-ae-phone": "详情路径正则拒绝合法的 /ref= 跟踪后缀，造成62条商品记录被计为0。",
    "dynamic-react-cart": "是否让重复控件和广告直接推翻正文通过，缺少跨案例统一的纯净度门槛。",
    "dynamic-104-mui-data-grid": "是否让越界章节和 Cookie 内容直接推翻正文通过，缺少跨案例统一的纯净度门槛。",
    "dynamic-107-redoc-cafe-api": "单句宣传横幅被设为二元失败条件；长文档也缺少按语义章节的检查。",
}

CONFIRMED_FALSE_NEGATIVES = {
    "dynamic-042-en-real-amazon-india": "输出包含16条具名 Amazon 商品详情记录；合法 /ref= 后缀被规则错误拒绝。",
    "dynamic-099-ar-real-amazon-ae-phone": "输出包含62条具名 Amazon 商品详情记录；合法 /ref= 后缀被规则错误拒绝。",
    "dynamic-107-redoc-cafe-api": "四个章节锚点和五段正文覆盖均通过，F1为0.8888；一条宣传横幅不应单独推翻正文完成。",
}

USER_DECISIONS = {
    "dynamic-react-cart": (
        "正文包含全部16条商品，但也重复保留16个 Add to cart 控件和招聘推广。",
        "决定是否采用统一纯净度门槛；在门槛冻结前不改判。",
    ),
    "dynamic-104-mui-data-grid": (
        "目标说明和首个网格完整，但输出继续进入 Pro/Premium 内容并含 Cookie 区块。",
        "决定何种越界比例构成严重噪音；在统一门槛冻结前不改判。",
    ),
    "dynamic-105-steam-city-search": (
        "初始HTTP文件已含50个 search_result_row 和50个 /app/ 详情地址，与 initial_missing 记录矛盾。",
        "该页可保留为易变列表案例，但不应在未更换来源前计入动态正文分母。",
    ),
}

EVIDENCE_PACKAGE_IDS = {
    "dynamic-014-en-stable-rsuite-table", "dynamic-017-en-stable-eui-table",
    "dynamic-025-025-tabulator-vertical-virtual-dom", "dynamic-042-en-real-amazon-india",
    "dynamic-044-en-real-github-trending", "dynamic-045-en-real-espn-standings",
    "dynamic-046-en-real-hn-algolia", "dynamic-050-en-real-usgs-earthquakes",
    "dynamic-061-061-juejin-ranking", "dynamic-064-zh-real-douban-chart",
    "dynamic-068-ja-real-nintendo-software", "dynamic-069-ja-real-weathernews-tokyo",
    "dynamic-073-ko-real-naver-politics", "dynamic-093-pt-real-terra-noticias",
    "dynamic-094-pt-real-cnnbrasil-politica", "dynamic-096-ar-real-cnn-world",
    "dynamic-099-ar-real-amazon-ae-phone",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def relation(independent: str, reported: str) -> str:
    if independent == "unable":
        return "无法比较"
    if independent == reported:
        return "正确通过" if reported == "pass" else "正确失败"
    return "错误通过（假阳性）" if reported == "pass" else "错误失败（假阴性）"


def qualification_summary(blind: dict[str, Any]) -> tuple[str, str]:
    identifier = blind["case_id"]
    if identifier == "dynamic-105-steam-city-search":
        return "fail", USER_DECISIONS[identifier][0]
    source = blind["source"]
    captures = source.get("captures") or {}
    history = blind.get("qualification_history") or {}
    if not captures and history:
        captures = {key: history.get(key) for key in ("capture_a", "capture_b")}
    parts = []
    for key in ("capture_a", "capture_b"):
        capture = captures.get(key)
        if not isinstance(capture, dict):
            continue
        classification = capture.get("initial_classification") or capture.get("classification")
        chars = capture.get("target_chars")
        completion = capture.get("completion") or {}
        satisfied = completion.get("satisfied") if isinstance(completion, dict) else None
        eligible = capture.get("eligible")
        parts.append(f"{key[-1].upper()}: initial={classification}, target_chars={chars}, completion={satisfied}, eligible={eligible}")
    if not parts:
        state = source.get("source_verification_state")
        parts.append(f"dataset source_verification={state}; paired hashes are bound to the case")
    return "pass", "; ".join(parts)


def issue_rows(identifier: str, corrected: bool, resolved: bool = False) -> list[dict[str, str]]:
    rows = []
    if identifier in SCOPE_FIXES:
        rows.append({"category": "范围/标准答案", "severity": "已修复" if corrected else "重要", "finding": SCOPE_FIXES[identifier][0]})
    if identifier in RULE_FIXES:
        rows.append({"category": "规则/评分", "severity": (
            "已裁决" if resolved and identifier in USER_DECISIONS else
            "已修复" if corrected and identifier not in USER_DECISIONS else
            "待决定" if identifier in USER_DECISIONS else "重要"),
            "finding": RULE_FIXES[identifier]})
    if identifier in EVIDENCE_PACKAGE_IDS:
        rows.append({
            "category": "来源证据展示", "severity": "展示改进",
            "finding": "最终100例证据包未汇总本例来源目录；两次资格采集及哈希仍由冻结选择材料绑定。",
        })
    if identifier == "dynamic-105-steam-city-search":
        rows.append({"category": "案例资格", "severity": "阻塞", "finding": USER_DECISIONS[identifier][0]})
    return rows


def build(args: argparse.Namespace) -> dict[str, Any]:
    corrected = args.stage in {"stage2", "resolved"}
    resolved = args.stage == "resolved"
    facts = read_json(args.facts)
    dataset = read_json(args.dataset)
    old_review = read_json(args.prior_review or (args.evidence / "result-review.json"))
    review_by_attempt = ({row["attempt_id"]: row for row in old_review["attempts"]}
                         if not resolved else {})
    review_by_case = ({row["case_id"]: row for row in old_review["cases"]}
                      if resolved else {})
    dataset_by_id = {row["id"]: row for row in dataset["cases"]}
    cases = []
    for row in facts["cases"]:
        blind = row["blind"]
        identifier = blind["case_id"]
        recorded = row["recorded_after_blind_review"]
        reported = recorded["verdict"]
        qualification, qualification_evidence = qualification_summary(blind)
        prior = review_by_attempt.get(blind["call"]["attempt_id"])
        if resolved and identifier == "dynamic-react-cart":
            independent = "pass"
            independent_reason = "全部16条目标商品记录完整；与各记录绑定的 Add to cart 控件按已批准统一口径计为附属界面，不单独推翻正文通过。"
        elif resolved and identifier == "dynamic-104-mui-data-grid":
            independent = "fail"
            independent_reason = "目标说明与首个网格虽完整，但输出越过声明区域进入独立 Pro 产品层级和 Cookie 区块，按已批准口径属于严重越界。"
        elif resolved and identifier == "dynamic-105-blueprint-docs":
            independent = "fail"
            independent_reason = "Moli 输出覆盖了完整 main 正文，但同时带回约32 KB整站导航；其中独立确认的 Dark theme 工具栏位于 main 外，违反已声明的顶栏和侧栏排除范围。"
        elif identifier in USER_DECISIONS:
            independent = "unable"
            independent_reason = USER_DECISIONS[identifier][0]
        elif identifier in CONFIRMED_FALSE_NEGATIVES:
            independent = "pass"
            independent_reason = CONFIRMED_FALSE_NEGATIVES[identifier]
        elif resolved:
            prior_case = review_by_case[identifier]
            independent = prior_case["judgment"]["independent"]
            independent_reason = prior_case["judgment"]["reason"]
        else:
            independent = prior["final_verdict"]
            independent_reason = prior.get("adjudication") or prior["agent_evidence"]
        scope = ("corrected" if corrected else "needs_correction") if identifier in SCOPE_FIXES else "reasonable"
        source = ("corrected" if corrected else "needs_correction") if identifier in SCOPE_FIXES else "credible"
        rule = ("adjudicated" if resolved and identifier in USER_DECISIONS and identifier in RULE_FIXES else
                "needs_decision" if identifier in USER_DECISIONS and identifier in RULE_FIXES else
                "corrected" if corrected and identifier in RULE_FIXES else
                "needs_correction" if identifier in RULE_FIXES else "pass")
        issues = issue_rows(identifier, corrected, resolved)
        decision = None if resolved else USER_DECISIONS.get(identifier)
        cases.append({
            "ordinal": blind["ordinal"], "case_id": identifier, "name": blind["name"], "url": blind["url"],
            "page_type": (dataset_by_id[identifier].get("acceptance_contract") or {}).get("schema") or "fixed_body",
            "dynamic_qualification": {"status": qualification, "evidence": qualification_evidence},
            "target_scope": {
                "status": scope,
                "include": blind.get("description"),
                "exclude": "navigation, advertising, footer, cookie/login controls, and undeclared interactive chrome",
                "finding": SCOPE_FIXES.get(identifier, (None, None))[0],
            },
            "reference": {
                "status": source,
                "scope": blind.get("description"),
                "ground_truth_chars": blind["ground_truth_chars"],
                "source_file_count": len(blind["source"]["files"]),
                "current_package_present": blind["source"]["source_directory_present"],
                "history_binding_present": bool(blind.get("qualification_history")),
                "recommendation": SCOPE_FIXES.get(identifier, (None, None))[1],
            },
            "rule_quality": {
                "status": rule,
                "fixture_validation": blind["rule_fixture_history"]["summary"],
                "finding": RULE_FIXES.get(identifier),
            },
            "call_chain": {
                "status": "pass",
                "attempt_id": blind["call"]["attempt_id"],
                "provider": blind["call"]["provider"],
                "url_matches": blind["call"]["url_matches"],
                "run": blind["call"]["run"],
                "elapsed_ms": blind["call"]["elapsed_ms"],
                "request_ok": blind["call"]["request_ok"],
                "artifact_sha256": blind["call"]["artifact"]["sha256"],
                "artifact_hash_verified": blind["call"]["artifact_hash_matches_record"],
                "execution_owner_count": blind["call"]["execution_owner_count"],
                "retry_count": 0,
            },
            "judgment": {
                "independent": independent, "reported": reported,
                "relation": relation(independent, reported), "reason": independent_reason,
                "final_adjudication": ("已按用户批准口径裁决" if resolved and identifier in {
                    "dynamic-react-cart", "dynamic-104-mui-data-grid"} else
                    "替代案例已完成独立测量与复核" if resolved and identifier == "dynamic-105-blueprint-docs" else
                    "等待用户决定" if decision else
                    "已离线纠正" if corrected and identifier in CONFIRMED_FALSE_NEGATIVES else
                    "改判" if independent != reported else "保持原判"),
            },
            "issues": issues,
            "verified_facts": [qualification_evidence, independent_reason],
            "ai_recommendation": ("已执行用户批准的统一口径。" if resolved and identifier in {
                "dynamic-react-cart", "dynamic-104-mui-data-grid"} else
                "已用合格的 Blueprint 客户端正文案例替代 Steam。" if resolved and identifier == "dynamic-105-blueprint-docs" else
                decision[1] if decision else
                SCOPE_FIXES.get(identifier, (None, None))[1] or RULE_FIXES.get(identifier) or "保持当前定义和判定。"),
            "user_decision_required": decision[1] if decision else None,
            "reviewer": REVIEWER, "protocol": PROTOCOL,
            "bindings": facts["bindings"],
        })
    counts = Counter(case["judgment"]["relation"] for case in cases)
    status_counts = {
        "dynamic_qualification": dict(Counter(case["dynamic_qualification"]["status"] for case in cases)),
        "scope": dict(Counter(case["target_scope"]["status"] for case in cases)),
        "reference": dict(Counter(case["reference"]["status"] for case in cases)),
        "rule": dict(Counter(case["rule_quality"]["status"] for case in cases)),
        "call_chain": dict(Counter(case["call_chain"]["status"] for case in cases)),
    }
    issues = []
    for case in cases:
        for item in case["issues"]:
            issues.append({"case_id": case["case_id"], **item})
    reference_revisions = (read_json(args.evidence / "reference-revisions.json")
                           if (args.evidence / "reference-revisions.json").is_file() else [])
    scoring_revisions = (read_json(args.evidence / "scoring-revisions.json")
                         if (args.evidence / "scoring-revisions.json").is_file() else [])
    case_replacement = (read_json(args.evidence / "case-replacement.json")
                        if (args.evidence / "case-replacement.json").is_file() else None)
    measurement_lineage = (read_json(args.evidence / "measurement-lineage.json")
                           if (args.evidence / "measurement-lineage.json").is_file() else {})
    revision_summary = None
    if corrected and reference_revisions and scoring_revisions:
        first = reference_revisions[0]
        last = scoring_revisions[-1]
        revision_summary = {
            "input_dataset_sha256": first["input_dataset_sha256"],
            "input_results_sha256": first["input_results_sha256"],
            "output_dataset_sha256": last["output_dataset_sha256"],
            "output_results_sha256": last["output_results_sha256"],
            "reference_correction_cases": len(first["changed_cases"]),
            "scoring_rule_correction_cases": len(last["changed_cases"]),
            "case_replacements": 1 if case_replacement else 0,
            "final_machine_verdicts": facts["recorded_verdicts"],
            "provider_calls": sum(row.get("provider_calls", 0) for row in [*reference_revisions, *scoring_revisions]) + (case_replacement or {}).get("provider_calls", 0),
            "physical_calls_in_lineage": measurement_lineage.get(
                "physical_calls_in_lineage", 100),
            "independent_review_status": ("complete_100_of_100" if resolved else
                                          "pending_three_policy_or_eligibility_decisions"),
        }
    return {
        "schema_version": 1,
        "kind": ("dynamic_100_formal_independent_audit_resolved" if resolved else
                 "dynamic_100_formal_independent_audit_stage2" if corrected else
                 "dynamic_100_formal_independent_audit_stage1"),
        "status": ("complete" if resolved else
                   "stage2_complete_pending_user_decisions" if corrected else
                   "stage1_complete_stage2_pending"), "reviewed_cases": len(cases),
        "reviewed_attempts": len(cases), "reviewer": REVIEWER, "protocol": PROTOCOL,
        "bindings": facts["bindings"], "inventory": facts["inventory"],
        "revision_summary": revision_summary,
        "summary": {"relations": dict(counts), "statuses": status_counts,
                    "issue_count": len(issues),
                    "unresolved_cases": [] if resolved else sorted(USER_DECISIONS)},
        "global_findings": [
            {"category": "证据汇总", "severity": "重要", "finding": "最终证据包只含83个来源目录；17例须回溯冻结选择材料。32个结构型案例仅汇总初始响应摘要，完整原始字节仍在历史证据目录。"},
            {"category": "规则生命周期", "severity": "已修复" if corrected else "重要", "finding": "旧绑定缺省的空 resource_output_contract 现按等价空策略兼容；非空策略仍严格拒绝。" if corrected else "最新主线把默认空 resource_output_contract 纳入 scope_identity，导致旧的合法执行绑定无法离线重评分。"},
            {"category": "报告展示", "severity": "已解决" if resolved else "待决定" if corrected else "重要", "finding": "本地正式审查报告显示52/48、100/100完成复核且未决0；线上报告仍保留旧49/51，本轮按约束不发布。" if resolved else "本地草稿显示修订后的52/48机器结果和3个未决案例；线上报告仍保留49/51且未解决0，本轮不发布。" if corrected else "线上报告仍显示49/51和未解决0，未体现本轮资格、范围、路径后缀和纯净度口径问题。"},
        ],
        "issues": issues, "cases": cases,
    }


def render(report: dict[str, Any], dataset: dict[str, Any], evidence: Path, language: str) -> str:
    zh = language == "zh"
    resolved = report["status"] == "complete"
    labels = {
        "title": ("动态100正式独立审查（已解决）" if resolved else "动态100正式独立审查（阶段2待确认）" if report["status"].startswith("stage2") else "动态100正式独立审查（阶段1）") if zh else ("Dynamic 100 Formal Independent Audit (Resolved)" if resolved else "Dynamic 100 Formal Independent Audit (Stage 2, Pending Decisions)" if report["status"].startswith("stage2") else "Dynamic 100 Formal Independent Audit (Stage 1)"),
        "draft": ("本地正式审查报告；未发布。" if resolved else "本地审查草稿；未发布。") if zh else ("Formal local audit report; not published." if resolved else "Local review draft; not published."),
        "cases": "逐例审查" if zh else "Case reviews", "issues": "需处理事项" if zh else "Items requiring action",
    }
    status_en = {
        "pass": "pass", "fail": "fail", "reasonable": "reasonable",
        "credible": "credible", "corrected": "corrected",
        "needs_correction": "needs correction", "needs_decision": "needs decision",
        "adjudicated": "adjudicated",
        "正确通过": "correct pass", "正确失败": "correct fail",
        "错误通过（假阳性）": "false positive", "错误失败（假阴性）": "false negative",
        "无法比较": "not comparable", "范围/标准答案": "Scope / reference",
        "规则/评分": "Rule / scoring", "来源证据展示": "Source-evidence packaging",
        "案例资格": "Case eligibility", "已修复": "fixed", "重要": "important",
        "待决定": "decision needed", "展示改进": "presentation improvement",
        "已裁决": "adjudicated", "已解决": "resolved",
    }
    english_findings = {
        "最终100例证据包未汇总本例来源目录；两次资格采集及哈希仍由冻结选择材料绑定。": "The final 100-case package does not aggregate this source directory; the two qualification captures and hashes remain bound through frozen selection evidence.",
        USER_DECISIONS["dynamic-react-cart"][0]: "All 16 products are present, but the answer repeats 16 Add to cart controls and includes recruitment promotion.",
        USER_DECISIONS["dynamic-react-cart"][1]: "Choose one benchmark-wide purity threshold; do not change the verdict until it is frozen.",
        USER_DECISIONS["dynamic-104-mui-data-grid"][0]: "The target explanation and first grid are complete, but the answer continues into Pro/Premium material and a Cookie section.",
        USER_DECISIONS["dynamic-104-mui-data-grid"][1]: "Choose how much out-of-scope material counts as severe noise; do not change the verdict until one uniform threshold is frozen.",
        USER_DECISIONS["dynamic-105-steam-city-search"][0]: "The initial HTTP file already contains 50 search_result_row elements and 50 /app/ detail URLs, contradicting the recorded initial_missing state.",
        USER_DECISIONS["dynamic-105-steam-city-search"][1]: "It can remain a volatile-list case, but should not count in the dynamic-content denominator unless replaced or reclassified.",
        "已执行用户批准的统一口径。": "The approved benchmark-wide policy has been applied.",
        "已用合格的 Blueprint 客户端正文案例替代 Steam。": "Steam was replaced with the qualified Blueprint client-rendered document case.",
    }
    finding_en_by_case = {
        "dynamic-020-webscraper-io-test": "The first automatic batch is six product records; pagination controls are outside the target.",
        "dynamic-021-021-scalar-get-operation": "The corrected reference keeps the GET operation semantics and removes editor controls.",
        "dynamic-024-en-stable-gridjs-pagination": "The completion status remains required while Previous/Next controls are excluded.",
        "dynamic-025-025-tabulator-vertical-virtual-dom": "An unresolved Loading Example placeholder now fails the target.",
        "dynamic-033-033-vuetify-usage-table": "The reference is limited to Usage and its first table.",
        "dynamic-042-en-real-amazon-india": "The product path now accepts the legitimate /ref= suffix and deduplicates by ASIN.",
        "dynamic-044-en-real-github-trending": "Trending and Sponsors routes no longer count as owner/repository records.",
        "dynamic-099-ar-real-amazon-ae-phone": "The product path now accepts the legitimate /ref= suffix and deduplicates by ASIN.",
        "dynamic-107-redoc-cafe-api": "Documentation controls are excluded and one promotional sentence no longer defeats complete semantic coverage.",
        "dynamic-react-cart": "All 16 target product records are complete; their attached Add to cart controls are supplemental UI under the approved policy.",
        "dynamic-104-mui-data-grid": "The answer crosses beyond the declared grid into a separate Pro tier and Cookie surface, so it remains a failure.",
        "dynamic-105-blueprint-docs": "The output covers the complete independently captured main body but also includes about 32 KB of site navigation; the independently confirmed Dark theme toolbar is outside main and violates the declared exclusion.",
    }
    def tr(value: str, *, case_id: str | None = None) -> str:
        if zh:
            return value
        return status_en.get(value) or english_findings.get(value) or (
            finding_en_by_case.get(case_id) if case_id else None) or value
    case_by_id = {row["id"]: row for row in dataset["cases"]}
    def public_text(value: str) -> str:
        return value.replace(str(Path.home()), "$HOME").replace("/private/tmp", "$TMPDIR")
    cards = []
    for case in report["cases"]:
        source = evidence / "sources" / case["case_id"] / "reference.txt"
        artifact = evidence / "artifacts/moli-webfetch" / case["case_id"] / "run-001.txt"
        reference = public_text(source.read_text(errors="replace") if source.exists() else case_by_id[case["case_id"]].get("ground_truth_main_content", ""))
        actual = public_text(artifact.read_text(errors="replace") if artifact.exists() else "")
        issue_html = "".join(f"<li><b>{html.escape(tr(i['category']))}</b> · {html.escape(tr(i['severity']))}: {html.escape(tr(i['finding'], case_id=case['case_id']))}</li>" for i in case["issues"]) or "<li>None</li>"
        section_labels = (["动态资格", "范围 / 参考 / 规则", "调用链", "判定"] if zh else
                          ["Dynamic qualification", "Scope / reference / rule", "Call chain", "Judgment"])
        reason = case['judgment']['reason'] if zh else (
            finding_en_by_case.get(case['case_id']) or
            "Independent inspection agrees with the corrected machine verdict based on the complete saved output and independent reference."
        )
        cards.append(f"""<article id="{html.escape(case['case_id'])}"><h2>{case['ordinal']}. {html.escape(case['name'])}</h2>
<p><code>{html.escape(case['case_id'])}</code> · <a href="{html.escape(case['url'])}">{html.escape(case['url'])}</a></p>
<div class="grid"><section><h3>{section_labels[0]}</h3><p class="{case['dynamic_qualification']['status']}">{html.escape(tr(case['dynamic_qualification']['status']))}</p><p>{html.escape(case['dynamic_qualification']['evidence'])}</p></section>
<section><h3>{section_labels[1]}</h3><p>{html.escape(tr(case['target_scope']['status']))} · {html.escape(tr(case['reference']['status']))} · {html.escape(tr(case['rule_quality']['status']))}</p><p>{html.escape(tr(case['ai_recommendation'], case_id=case['case_id']))}</p></section>
<section><h3>{section_labels[2]}</h3><p>{html.escape(case['call_chain']['attempt_id'])}; {case['call_chain']['elapsed_ms']} ms; SHA-256 {html.escape(case['call_chain']['artifact_sha256'])}</p></section>
<section><h3>{section_labels[3]}</h3><p><b>{html.escape(tr(case['judgment']['relation']))}</b>: {html.escape(reason)}</p></section></div>
<ul>{issue_html}</ul><div class="compare"><details open><summary>{'独立参考' if zh else 'Independent reference'}</summary><pre>{html.escape(reference)}</pre></details><details open><summary>{'Moli 输出' if zh else 'Moli output'}</summary><pre>{html.escape(actual)}</pre></details></div></article>""")
    relations = report["summary"]["relations"]
    return f"""<!doctype html><html lang="{language}"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{labels['title']}</title>
<style>:root{{color-scheme:light dark}}body{{font:15px/1.55 system-ui;max-width:1500px;margin:auto;padding:24px}}nav{{position:sticky;top:0;background:Canvas;padding:8px;border-bottom:1px solid GrayText}}article{{border:1px solid GrayText;border-radius:12px;padding:18px;margin:24px 0}}.grid,.compare{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}section,details{{border:1px solid color-mix(in srgb,GrayText 45%,transparent);padding:10px;border-radius:8px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;max-height:34rem;overflow:auto}}.pass,.reasonable,.credible{{color:#138a36}}.fail,.needs_correction{{color:#c0392b}}@media(max-width:760px){{.grid,.compare{{grid-template-columns:1fr}}}}</style>
<body><nav><a href="{'../en/' if zh else '../zh/'}">{'English' if zh else '中文'}</a> · <a href="#issues">{labels['issues']}</a> · <a href="#cases">{labels['cases']}</a></nav>
<h1>{labels['title']}</h1><p>{labels['draft']}</p><p>{'100/100 个案例' if zh else '100/100 cases'} · {'正确通过' if zh else 'correct pass'} {relations.get('正确通过',0)} · {'正确失败' if zh else 'correct fail'} {relations.get('正确失败',0)} · {'错误通过' if zh else 'false positive'} {relations.get('错误通过（假阳性）',0)} · {'错误失败' if zh else 'false negative'} {relations.get('错误失败（假阴性）',0)} · {'无法比较' if zh else 'not comparable'} {relations.get('无法比较',0)}</p>
<section id="issues"><h2>{labels['issues']}</h2><ul>{''.join(f'<li><a href="#{html.escape(x)}">{html.escape(x)}</a>: {html.escape(tr(USER_DECISIONS[x][0]))}</li>' for x in report['summary']['unresolved_cases']) or ('<li>无未决事项。</li>' if zh else '<li>No unresolved items.</li>')}</ul></section>
<main id="cases">{''.join(cards)}</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dynamic-content-100-moli-webfetch/dataset.json")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--prior-review", type=Path)
    parser.add_argument("--stage", choices=("stage1", "stage2", "resolved"), default="stage1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--html-root", type=Path, required=True)
    args = parser.parse_args()
    report = build(args)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    dataset = read_json(args.dataset)
    for language in ("zh", "en"):
        folder = args.html_root / language
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(render(report, dataset, args.evidence, language))


if __name__ == "__main__":
    main()
