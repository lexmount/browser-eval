#!/usr/bin/env python3
# ruff: noqa: E501
"""Build the 100-site independent review from frozen and fresh evidence."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

from harness.web_fetch.dynamic_lists import extract_named_entries
from harness.web_fetch.metrics import fact_in_text, normalize_for_match, task_passed
from harness.web_fetch.scoring import score_attempt

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/popular-sites-dataset.json"
RUN = Path.home() / ".browser-eval/evidence/research/popular-sites-lexmount-2026-09-11-current-run-001"
LIVE = Path.home() / ".browser-eval/evidence/research/popular-sites-independent-review-2026-09-14/current-browser-observations.json"
SECOND = Path.home() / ".browser-eval/evidence/research/popular-sites-independent-review-2026-09-14/accepted-rule-changes-second-observation.json"
OUTPUT = ROOT / "research/popular-sites-independent-review-2026-09-14.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def composite(observation: dict) -> str:
    return " ".join(
        [observation.get("title") or "", observation.get("visible_text") or ""]
        + [item.get("text", "") for item in observation.get("headings", [])]
        + [item.get("label", "") for item in observation.get("controls", [])]
        + [item.get("text", "") for item in observation.get("links", [])]
    )


def anchor_results(case: dict, text: str) -> list[bool]:
    return [
        any(fact_in_text(text, value) for value in anchor["alternatives"])
        for anchor in case["acceptance_contract"].get("stable_text_anchors", [])
    ]


def current_state(case: dict, observation: dict) -> tuple[str, list[bool]]:
    text = composite(observation)
    folded = normalize_for_match(text)
    results = anchor_results(case, text)
    title = normalize_for_match(observation.get("title") or "")
    visible = normalize_for_match(observation.get("visible_text") or "")
    unavailable = (
        observation.get("observation_state") != "captured"
        or (observation.get("final_url") or "").startswith("chrome-error:")
        or observation.get("http_status") in {403, 429}
        or len(visible) < 20
        or title in {"human verification", "just a moment...", "验证中心", "安全验证 - 知乎"}
        or any(
            marker in folded
            for marker in (
                "performing security verification",
                "please wait while we perform security check",
                "system updating to better serve you",
                "服务区域限制 prime video",
                "we need your consent in order to provide our services",
                "身份核实 请向右拖动滑块",
                "请您登录后查看更多专业优质内容",
            )
        )
    )
    if unavailable:
        return "当前无法复核", results
    if all(results):
        return "当前全部存在", results
    if any(results):
        return "当前部分存在", results
    return "当前均未观察到", results


def live_named_count(case: dict, observation: dict) -> int | None:
    if case["acceptance_contract"]["schema"] != "named_link":
        return None
    links = [
        {"title": item["text"], "url": item["url"]}
        for item in observation.get("links", [])
    ]
    return len(extract_named_entries("", case["acceptance_contract"], case["url"], links))


SPECIAL = {
    "global-homepage-010-bing-com": {
        "candidate": "已接受：删除两次均未观察到的 `Search using an image`；剩余四组不硬补替代项",
        "quantity": "数量够，但图像搜索控制存在地区/A-B 漂移",
        "action": "本地规则已修；旧输出已离线重评分",
    },
    "global-homepage-038-samsung-com": {
        "candidate": "现有规则调整：固定英文地区 URL，或为主要品类建立同语义中文替代组",
        "quantity": "5 组足够，但语言和地区没有被 URL 锁定",
        "action": "先决定地区契约；URL 改变后需申请重跑",
    },
    "global-homepage-047-booking-com": {
        "candidate": "证据不足：当前匿名首次加载为 consent 页面，不新增",
        "quantity": "5 组不算少，问题是同意页与地区条件未锁定",
        "action": "补同地区独立证据；改变 URL/交互政策后才重跑",
    },
    "global-homepage-080-cnn-com": {
        "candidate": "已接受：删除两次均未观察到的 `CNN UNDERSCORED` 和 `Across our coverage`；保留身份与结构",
        "quantity": "原 4 组偏严；两个版块名均随改版消失",
        "action": "本地规则已修；旧输出已离线重评分",
    },
    "global-homepage-088-primevideo-com": {
        "candidate": "证据不足：先固定可服务地区 URL；不要从区域限制页补文字",
        "quantity": "4 组足够，但可访问地区未锁定",
        "action": "URL/地区契约改变后需申请重跑",
    },
    "challenge-04": {
        "candidate": "保持不加入：继续仅验证知乎问题详情链接结构",
        "quantity": "0 组可以接受，因为 named_link 结构本身是主要求",
        "action": "保持；当前登录/安全页只记为无法复核",
    },
    "challenge-09": {
        "candidate": "保持不加入：Ranking、IMDb rating 等排序控件",
        "quantity": "2 组加至少 3 条详情链接已能确认目标列表",
        "action": "保持；当前遇到 Human Verification",
    },
    "china-homepage-063-sina-com-cn": {
        "candidate": "保持不加入：稳定文字无需增删；修正整页 `Not Found` 子串误杀",
        "quantity": "4 组加 27 条具名链接已经充分",
        "action": "本地评分修正已完成；正式发布前绑定结果复核",
    },
    "china-homepage-080-yandex-com": {
        "candidate": "已接受：删除两次均未观察到的 `Images`；剩余三组不硬补替代项",
        "quantity": "4 组足够；单个导航入口不应决定整页成败",
        "action": "本地规则已修；旧输出已离线重评分",
    },
    "china-homepage-081-elsevier-com": {
        "candidate": "现有规则调整：固定英文 URL，或增加中文同语义组，而不是新增更多英文文字",
        "quantity": "4 组足够，但根 URL 当前重定向到中文版",
        "action": "先决定语言契约；URL 改变后需申请重跑",
    },
}


KNOWN_OMISSIONS = {
    "challenge-01": "保持不加入：`In Stock`、`All Filters` 同属筛选 UI，加入只增加脆弱性",
    "challenge-05": "保持不加入：`我没看过的` 是账户/状态型控件",
    "challenge-09": "保持不加入：`Ranking`、`IMDb rating`、`Release date` 等排序项冗余",
    "challenge-10-justwatch": "保持不加入：`Watch series for free` 是易改的 SEO 文案，现有三组已足够",
}


BODY_CANDIDATES = {
    "global-homepage-008-whatsapp-com", "global-homepage-021-netflix-com",
    "global-homepage-023-cloud-microsoft", "global-homepage-028-microsoft-com",
    "global-homepage-038-samsung-com", "global-homepage-041-discord-com",
    "global-homepage-042-github-com", "global-homepage-046-zoom-us",
    "global-homepage-049-brave-com", "global-homepage-063-paypal-com",
    "global-homepage-067-deepseek-com", "global-homepage-075-telegram-org",
    "global-homepage-086-usps-com", "global-homepage-087-instructure-com",
    "china-homepage-012-feishu-cn", "china-homepage-026-aliyun-com",
    "china-homepage-033-dingtalk-com", "china-homepage-050-openai-com",
    "china-homepage-052-alipay-com", "china-homepage-059-jianying-com",
    "china-homepage-065-lingxing-com", "china-homepage-075-volcengine-com",
    "china-homepage-081-elsevier-com", "china-homepage-082-huawei-com",
}


def historical_status(case: dict, global_provenance: dict, china_capture: dict) -> str:
    case_id = case["id"]
    if case_id.startswith("global-homepage-"):
        row = global_provenance[case_id]
        return "已验证：独立证据支持全部文字" if row["unsupported_literal_count"] == 0 else "存在无来源文字"
    if case_id.startswith("china-homepage-"):
        observed = china_capture[case_id]
        return (
            "已验证：独立 Chromium 证据支持全部文字"
            if all(anchor_results(case, composite(observed)))
            else "存在无来源文字"
        )
    anchors = case["acceptance_contract"].get("stable_text_anchors", [])
    if not anchors:
        return "无稳定文字；仅结构参考"
    base = ROOT / "evaluation/sources/popular-sites" / case_id
    source = base / "stable-page.txt"
    if not source.exists():
        source = ROOT / "evaluation/sources" / case_id / "reference.txt"
    return (
        "已验证：独立旧快照支持全部文字"
        if source.exists() and all(anchor_results(case, source.read_text()))
        else "证据链不完整"
    )


def modified_score(case: dict, row: dict, remove_anchor_ids: set[str] = frozenset(), remove_not_found: bool = False) -> bool:
    revised = deepcopy(case)
    revised["acceptance_contract"]["stable_text_anchors"] = [
        anchor for anchor in revised["acceptance_contract"].get("stable_text_anchors", [])
        if anchor["id"] not in remove_anchor_ids
    ]
    if remove_not_found:
        revised["forbidden_any"] = [value for value in revised["forbidden_any"] if value != "Not Found"]
    artifact = RUN / row["artifact_path"]
    metrics = score_attempt(
        revised,
        artifact.read_bytes(),
        ok=row["ok"],
        metadata=row.get("provider_metadata") or {},
        status_code=row.get("status_code"),
    )
    return task_passed(row["ok"], metrics, revised["task_kind"])


def main() -> None:
    if not LIVE.exists() or not SECOND.exists():
        raise SystemExit(f"Missing fresh capture: {LIVE} or {SECOND}")
    dataset = json.loads(DATASET.read_text())
    cases = dataset["cases"]
    frozen_dataset = json.loads((RUN / "dataset.json").read_text())
    frozen_cases = {case["id"]: case for case in frozen_dataset["cases"]}
    live_payload = json.loads(LIVE.read_text())
    live = {row["id"]: row for row in live_payload["observations"]}
    second_payload = json.loads(SECOND.read_text())
    live.update({row["id"]: row for row in second_payload["observations"]})
    results = [json.loads(line) for line in (RUN / "results.jsonl").read_text().splitlines() if line]
    result_by_id = {row["case_id"]: row for row in results}
    review = json.loads((RUN / "result-review.json").read_text())
    summary = json.loads((RUN / "summary.json").read_text())
    provenance_payload = json.loads((ROOT / "research/global-homepage-rule-provenance-audit-2026-09-11.json").read_text())
    global_provenance = {row["case_id"]: row for row in provenance_payload["cases"]}
    china_payload = json.loads((ROOT / "research/china-homepage-rule-evidence-2026-09-11.json").read_text())
    china_capture = {row["id"]: row for row in china_payload["observations"]}

    assert len(cases) == len(live) == len(results) == 100
    assert dataset["comparison"]["source_rule_registry_sha256"] == sha(ROOT / "research/popular-sites-rules.json")
    assert review["reviewed_attempts"] == review["expected_attempts"] == 100
    assert review["unresolved"] == 0
    assert summary["providers"]["lexmount-global"]["task_pass_rate"] == 0.42
    for row in results:
        artifact = RUN / row["artifact_path"]
        assert sha(artifact) == row["metrics"]["content_sha256"]
        report_copy = ROOT / "reports/webfetch" / row["artifact_path"]
        assert report_copy.read_bytes() == artifact.read_bytes()

    states: dict[str, int] = {}
    history: dict[str, int] = {}
    table_rows = []
    schema_counts: dict[str, int] = {}
    anchor_counts: dict[int, int] = {}
    for case in cases:
        case_id = case["id"]
        schema = case["acceptance_contract"]["schema"]
        schema_counts[schema] = schema_counts.get(schema, 0) + 1
        count = len(case["acceptance_contract"].get("stable_text_anchors", []))
        anchor_counts[count] = anchor_counts.get(count, 0) + 1
        historic = historical_status(case, global_provenance, china_capture)
        history[historic] = history.get(historic, 0) + 1
        state, matches = current_state(case, live[case_id])
        states[state] = states.get(state, 0) + 1
        link_count = live_named_count(case, live[case_id])
        special = SPECIAL.get(case_id, {})
        omission = special.get("candidate") or KNOWN_OMISSIONS.get(case_id) or "证据不足：单次新鲜观察未形成可证明长期稳定的新增候选（非穷举）"
        if "quantity" in special:
            quantity = special["quantity"]
        elif schema == "named_link":
            quantity = "足够：文字与至少 3 条唯一链接共同验收" if count else "仅结构；不应为了数量编造文字"
        else:
            quantity = "足够：已覆盖页面身份和主要功能" if count >= 3 else "需要补充独立身份/功能证据"
        action = special.get("action", "保持现有数量；定期复核漂移")
        confidence = "高" if state == "当前全部存在" else "中" if state == "当前部分存在" else "低"
        matched = f"{sum(matches)}/{len(matches)}" if matches else "—"
        links = "—" if link_count is None else str(link_count)
        body = "候选（须建立完整参考并单列任务族）" if case_id in BODY_CANDIDATES else "不建议用于正文匹配"
        table_rows.append(
            f"| `{case_id}` | {schema} | {count} | {historic} | {state} {matched} | {links} | "
            f"{quantity} | {omission} | {body} | {action} | {confidence} |"
        )

    simulations = [
        ("global-homepage-010-bing-com", {"stable-anchor-4"}, False, "移除当前已消失的图像搜索文字"),
        ("global-homepage-080-cnn-com", {"stable-anchor-3", "stable-anchor-4"}, False, "移除两次均未观察到的两个版块名"),
        ("china-homepage-080-yandex-com", {"stable-anchor-3"}, False, "移除当前已消失的 Images"),
        ("china-homepage-063-sina-com-cn", set(), True, "不让嵌入区块的 Not Found 否决整页"),
    ]
    simulation_lines = []
    changed_passes = 0
    for case_id, removed, remove_not_found, change in simulations:
        case = frozen_cases[case_id]
        old = task_passed(result_by_id[case_id]["ok"], result_by_id[case_id]["metrics"], case["task_kind"])
        new = modified_score(case, result_by_id[case_id], removed, remove_not_found)
        changed_passes += int(not old and new)
        simulation_lines.append(f"| `{case_id}` | {change} | {'通过' if old else '失败'} | {'通过' if new else '失败'} |")

    backup_manifest = Path.home() / ".browser-eval/backups/artifact-hub/l9jZU6GPMLn6/ver_51c5b0540707484a84fc1aa8c5e7e42c/backup-manifest.json"
    backup = json.loads(backup_manifest.read_text())
    captured = sum(row["observation_state"] == "captured" for row in live.values())
    http_200 = sum(row.get("http_status") == 200 for row in live.values())
    anchor_distribution = "、".join(
        f"{count} 组 {cases_count} 例" for count, cases_count in sorted(anchor_counts.items())
    )

    lines = [
        "# 全球热门网站 100 站 Web Fetch 独立审查",
        "",
        f"> 审查日期：{date.today().isoformat()}。本文件是审查与本地规则修订记录；没有发起 Lexmount 供应商调用，也没有修改线上报告。",
        "",
        "## 结论",
        "",
        "1. **报告内部完整，但不能原样视为最终可靠。** 本地检查确认 100 个任务、100 条结果、424 条逻辑路由和全部链接一致；线上分享页也显示同一站点、100/100 调用和 42/100 通过。",
        "2. **没有发现 Codex 凭空编造稳定文字。** 52 个全球首页已有逐字来源审计且 unsupported literal 为 0；40 个中国首页全部文字可在供应商之外的独立 Chromium 证据中找到；7 个带文字的旧列表案例可回溯到独立快照，第 8 个知乎案例明确不设文字。",
        "3. **但“曾经出现”不等于“长期稳定”。** 历史证据证明文字在取证时真实存在，不保证网页以后不改版。本次重新访问 100 个 URL，并对拟调整的 5 站做第二次、更长等待的观察；Bing、CNN、Yandex 的部分文字连续缺失，豆包与 Naver 则证实只是延迟加载，不应修改。地区重定向、同意页和安全验证另记为当前无法复核。",
        f"4. **3–5 个不是目标数量，也不是明显过少。** 本地修订后的分布为 {anchor_distribution}。因为所有组必须全部命中，盲目增加文字会提高假失败率；应追求身份、主要功能和结构的语义覆盖，而非数量。",
        "5. **确认并在本地修正一个评分误杀。** 新浪首页满足 4/4 稳定文字和 27 条有效具名链接，但一个嵌入模块返回 404，原规则因通用 `Not Found` 子串判整页失败。本地规则已移除该无上下文禁止项；真正的错误页仍会因为缺少页面身份或结构而失败。离线修正后该例从失败变为通过。",
        "6. **正文匹配没有被删掉。** 代码仍实现最多 5 段、每段 80% 目标、60% 下限、最多一段低于 80% 的正文覆盖。本次 100 站完整正文参考为 0/100，因此合法地没有启用；把少量锚点冒充完整正文会产生错误指标。",
        "",
        "## benchmark-report skill 的审核步骤",
        "",
        "该 skill 要求按 `案例/参考 → 单次调用与原始产物 → 归一化/评分 → 独立复核 → 发布版本` 追溯。审核时既看机器失败，也看机器通过；核对每次调用的完整输出和不可变身份；参考不足时指标应标为不可用；只改评分时离线重算，只有 URL、输入、供应商路线或验收契约实质变化时才重跑。",
        "",
        "## 已验证事实",
        "",
        f"- 当前矩阵：`{schema_counts['page_identity']}` 个 `page_identity`、`{schema_counts['named_link']}` 个 `named_link`。",
        f"- 本轮新鲜浏览器观察：100/100 已尝试，并对 5 个拟调整案例进行了第二次 10 秒等待观察；首轮 {captured}/100 保存了页面观察，{http_200}/100 首个响应为 HTTP 200。以第二次观察覆盖相应案例后：" + "、".join(f"{key} {value}" for key, value in states.items()) + "。",
        f"- 冻结结果：请求成功率 {summary['providers']['lexmount-global']['transport_success_rate']:.0%}，任务通过率 {summary['providers']['lexmount-global']['task_pass_rate']:.0%}。",
        f"- 盲审记录：{review['reviewed_attempts']}/{review['expected_attempts']}，分歧 {review['disagreements']}，未决 {review['unresolved']}。按用户决定，继续沿用报告中的“Codex 已核验”表述，不纳入本次修改。",
        f"- 发布备份：站点 `{backup['slug']}`、版本 `{backup['version_id']}`、归档 SHA-256 `{backup['archive_sha256']}`；清单声明发布前后均核验为当前版本。",
        "- 原发布版的 `make report-check` 已通过：100 条结果、424 条逻辑路由、0 个断链；本地规则修订尚未发布。",
        "",
        "## 主要问题与建议",
        "",
        "### P1：新浪整页错误判断已在本地修正",
        "",
        "原 `forbidden_any` 把正文任意位置的 `Not Found` 当作整页错误。新浪输出主体完整，只是一个嵌入区块 404。本地生成链已移除该通用子串规则；此修正不需要供应商调用，离线重评分已完成，正式发布前仍需把修订结果审查绑定完成。",
        "",
        "### P1：锁定地区和语言条件",
        "",
        "Samsung 根 URL 当前呈现中文版，Elsevier 根 URL 当前重定向到 `elsevier.cn`，Prime Video 返回地区限制，Booking.com 首次加载为 consent 页面。英文稳定文字本身有历史来源，但当前 URL 没有稳定地约束同一个页面变体。应固定语言/地区 URL，或把经过独立验证的同语义本地化文字放入同一 alternatives 组。URL 改动后才需要申请供应商重跑。",
        "",
        "### P2：移除经两次观察确认易漂移的单点控制/版块文字",
        "",
        "第二次独立观察再次未发现 Bing 的 `Search using an image`、CNN 的 `CNN UNDERSCORED / Across our coverage`、Yandex 的 `Images`；删除这些组后，剩余页面身份和结构仍充分，不需要为维持数量补文字。豆包的 `图像生成` 在 10 秒等待后出现，因此保留。",
        "",
        "### P2：Naver 重新取证已完成，不修改契约",
        "",
        "首轮 3 秒等待只看到 NAVER 和搜索框；第二次等待 10 秒后，`공지사항 / Partners / Developers` 全部出现，并有 8 条链接满足现有 host/path 契约。先前结论是延迟加载造成的假警报，现有稳定文字和 `named_link` 规则保持不变。",
        "",
        "## 稳定文字数量判断",
        "",
        "- `page_identity` 页面通常 3–5 个语义分散的文字已经足够：至少包含页面/品牌身份，再包含两个不同的核心功能或长期导航区。",
        "- `named_link` 页面还要求至少 3 条唯一名称＋合规详情 URL，因此 文字只负责确认页面上下文，不需要覆盖所有稳定菜单。",
        "- 未选中的文字只有在跨时间、同 URL/地区、供应商之外重复观察到，而且增加了新的语义维度时才值得加入。重复页脚、排序控件、账户状态和 SEO 文案不应加入。",
        "- 因为当前规则是全部组必须命中，增加锚点不会单调提高可靠性；它提高召回内容要求，同时降低对合法页面变体的容忍度。",
        "",
        "## 正文匹配结论",
        "",
        "项目的 `body_requirements` v3 会把完整参考分成最多 5 个区域，以连续六字符片段计算覆盖；所有区域至少 60%，且除最多一个区域外均须达到 80%。当前 100 站的 `ground_truth_main_content` 全为空、`content_score_applicable` 全为 false，报告也正确显示完整参考覆盖 0/100。",
        "",
        "这不是实现遗漏，而是参考范围决定的：热门首页会变化，现有证据只证明少量稳定文字和结构，不能代表完整正文。对品牌落地页可以新建一个静态正文子任务族，但必须先保存完整、可重建的参考正文，并与入口页/门户/内容列表分榜。不能在现有 100 站总榜里直接追加正文阈值。",
        "",
        "## 离线评分影响模拟",
        "",
        "以下只修改评分输入并复用原始供应商产物，没有发起新调用。用户已接受这些修改，第二次独立观察也已完成：",
        "",
        "| 案例 | 假设变化 | 当前 | 模拟后 |",
        "|---|---|---:|---:|",
        *simulation_lines,
        "",
        f"按已接受且证据支持的 Bing、CNN、Yandex 与新浪修改离线重评分，总通过数会由 42/100 变为 {42 + changed_passes}/100；豆包和 Naver 不修改。",
        "",
        "## 供应商调用判断",
        "",
        "当前不需要立即调用供应商：新浪可离线修正，锚点删除也可用旧产物评估。Samsung、Elsevier、Prime Video、Booking.com 若最终改变 URL、语言/地区参数或允许的交互范围，才需要新调用。届时应逐例说明变更、调用次数和费用风险并等待确认。",
        "",
        "## 100 站逐项审查表",
        "",
        "`当前` 一栏检查页面标题、可见正文、标题节点、控件标签和链接文字；它证明本次观察状态，不单独证明长期稳定。`当前链接` 只计算符合该例 host/path 契约的具名链接。",
        "",
        "| 案例 | 规则 | 文字组 | 历史来源 | 当前 | 当前链接 | 数量评价 | 遗漏候选/建议 | 正文匹配 | 动作 | 置信度 |",
        "|---|---|---:|---|---|---:|---|---|---|---|---:|",
        *table_rows,
        "",
        "## 审查边界",
        "",
        "- 本次当前页面观察来自单一日期和网络位置；拟删锚点做了第二次较长等待观察，但仍不能证明未来稳定。",
        "- 403、验证码、超时、地区限制和同意页均记为无法复核，没有据此断言历史文字不存在。",
        "- 当前网页观察不替代供应商结果；供应商输出也没有被用作稳定文字的来源。",
        "- 新鲜浏览器观察保存在 Git 外的 `~/.browser-eval/evidence/research/popular-sites-independent-review-2026-09-14/current-browser-observations.json`。",
        "- 第二次观察保存在同目录的 `accepted-rule-changes-second-observation.json`。",
        "- 已在本地规则和数据集生成链中落实 Bing、CNN、Yandex 锚点删除及通用 `Not Found` 误杀修正；旧证据保持不变，`reports/webfetch/` 未改，也没有发布新版本。",
    ]
    OUTPUT.write_text("\n".join(lines) + "\n")
    print(f"Wrote {OUTPUT}")
    print(json.dumps({"states": states, "history": history, "simulated_pass_gain": changed_passes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
