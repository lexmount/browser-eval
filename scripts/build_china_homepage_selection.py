#!/usr/bin/env python3
# ruff: noqa: E501 -- generated Chinese review prose is intentionally kept whole
"""Freeze the reviewed China-ranked homepage selection and human review view."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "research/china-homepage-candidates-2026-09-11.json"
OBSERVATIONS = ROOT / "research/china-site-root-observations-2026-09-11.json"
OUTPUT = ROOT / "research/china-homepage-selection.json"
REVIEW = ROOT / "research/china-homepage-selection-review.md"


SELECTED = {
    "doubao.com": "landing_page",
    "feishu.cn": "landing_page",
    "163.com": "content_list",
    "taobao.com": "product_catalog",
    "aliyun.com": "landing_page",
    "weibo.com": "content_list",
    "360.com": "landing_page",
    "dingtalk.com": "landing_page",
    "hao123.com": "content_list",
    "1688.com": "product_catalog",
    "quark.cn": "landing_page",
    "kdocs.cn": "landing_page",
    "kimi.com": "landing_page",
    "tmall.com": "product_catalog",
    "wedoany.com": "product_catalog",
    "qianwen.com": "landing_page",
    "douyu.com": "content_list",
    "openai.com": "landing_page",
    "alipay.com": "landing_page",
    "sohu.com": "content_list",
    "huya.com": "content_list",
    "cnki.net": "content_list",
    "nih.gov": "content_list",
    "huaban.com": "content_list",
    "jianying.com": "landing_page",
    "eastmoney.com": "content_list",
    "sina.com.cn": "content_list",
    "gamer520.com": "content_list",
    "lingxing.com": "landing_page",
    "alibaba.com": "product_catalog",
    "youdao.com": "landing_page",
    "3dmgame.com": "content_list",
    "2345.com": "content_list",
    "cctv.com": "content_list",
    "so.com": "landing_page",
    "volcengine.com": "landing_page",
    "nga.cn": "content_list",
    "yandex.com": "landing_page",
    "elsevier.com": "landing_page",
    "huawei.com": "landing_page",
}


INELIGIBLE = {
    "msn.cn": (
        "consent_wall_only",
        "首次无交互加载只呈现 Cookie/隐私同意层，缺少可用于任务验收的首页主体。",
    ),
    "google.com.hk": ("duplicate_final_page", "与现有 google.com 案例的自然落地首页相同。"),
    "facebook.com": ("login_page", "首页主体是登录和注册表单。"),
    "instagram.com": ("login_page", "首页主体是登录和注册表单。"),
    "x.com": ("access_denied", "HTTP 403 且可见正文为空。"),
    "woa.com": ("blank_or_error", "最终进入 Chromium 错误页且正文为空。"),
    "linkedin.com": ("login_page", "页面标题和主要操作均为登录或注册。"),
    "pinterest.com": ("login_page", "匿名首页包含密码登录/注册界面；沿用现有全球榜的排除口径。"),
    "tencent.com": ("capture_error", "独立 Chromium 未取得可审阅页面。"),
    "larkoffice.com": ("duplicate_final_page", "自然重定向到 feishu.cn，与已选飞书首页完全重合。"),
    "tiktok.com": ("capture_error", "独立 Chromium 未取得可审阅页面。"),
    "claude.ai": ("login_page", "自然重定向到 /login。"),
    "alibaba-inc.com": ("capture_error", "独立 Chromium 未取得可审阅页面。"),
    "live.com": ("blank", "自然重定向到 Outlook，但渲染后正文长度为 0。"),
    "bytedance.net": ("capture_error", "独立 Chromium 未取得可审阅页面。"),
    "msn.com": ("blank", "HTTP 200 但渲染后正文长度为 0。"),
    "sciencedirect.com": ("access_denied", "HTTP 403，页面只显示请求错误与支持编号。"),
    "temu.com": ("login_page", "自然重定向到 /login.html。"),
    "antfin.com": ("blank_or_error", "最终进入 Chromium 错误页且正文为空。"),
    "qcc.com": ("blank_or_error", "最终进入 Chromium 错误页且正文为空。"),
    "zhipin.com": ("blank", "HTTP 200 但最终为 about:blank，正文为空。"),
    "aliyuncs.com": ("not_found", "根页返回 HTTP 404。"),
    "office.com": (
        "duplicate_final_page",
        "自然重定向到 m365.cloud.microsoft，与现有 cloud.microsoft 案例重合。",
    ),
    "360.cn": (
        "duplicate_final_page",
        "与已选 360.com 自然落到同一套 360 官网内容；不作为独立网站案例重复计数。",
    ),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact_observation(item: dict) -> dict:
    return {
        key: item.get(key)
        for key in (
            "observation_state",
            "http_status",
            "final_url",
            "title",
            "visible_text_length",
            "visible_text_excerpt",
            "link_count",
            "same_origin_link_count",
            "has_password_input",
            "has_login_language",
            "has_captcha_language",
            "has_error_language",
            "navigation_error",
        )
    }


def main() -> None:
    candidates = json.loads(CANDIDATES.read_text())
    observations = json.loads(OBSERVATIONS.read_text())
    by_domain = {item["domain"]: item for item in observations["observations"]}
    ranked = {item["domain"]: item for item in candidates["candidates"]}
    all_domains = set(ranked)
    if len(SELECTED) != 40:
        raise SystemExit(f"Expected exactly 40 selected domains, got {len(SELECTED)}")
    if set(SELECTED) & set(INELIGIBLE):
        raise SystemExit("Selected and ineligible decisions overlap")
    if not (set(SELECTED) | set(INELIGIBLE)) <= all_domains:
        raise SystemExit("Decision table contains a domain outside the candidate pool")
    if set(by_domain) != all_domains:
        raise SystemExit("Observation batch does not exactly cover the candidate pool")

    selected = []
    ineligible = []
    reserves = []
    for domain, item in sorted(ranked.items(), key=lambda pair: pair[1]["china_rank"]):
        observation = compact_observation(by_domain[domain])
        if domain in SELECTED:
            selected.append(
                {
                    "id": f"china-homepage-{item['china_rank']:03d}-{domain.replace('.', '-')}",
                    "china_rank": item["china_rank"],
                    "domain": domain,
                    "url": item["url"],
                    "label": SELECTED[domain],
                    "selection_reason": "中国榜中按名次顺序遇到的合格、未重复公开首页。",
                    "evidence": observation,
                    "rule_status": "independent_rule_evidence_capture_required",
                }
            )
        elif domain in INELIGIBLE:
            reason_code, reason = INELIGIBLE[domain]
            ineligible.append(
                {
                    **item,
                    "reason_code": reason_code,
                    "reason": reason,
                    "evidence": observation,
                }
            )
        else:
            reserves.append(
                {
                    **item,
                    "reason_code": "eligible_reserve_after_target_met",
                    "reason": "在更高名次候选已补足 40 个后保留为替补，不进入本轮主集。",
                    "evidence": observation,
                }
            )

    if selected[-1]["china_rank"] != 82:
        raise SystemExit("Expected the strict 40-site target to be met at China rank 82")

    result = {
        "schema_version": 1,
        "purpose": "Human-review selection for expanding the existing popular-site union to 100 unique site domains",
        "source": {
            "candidate_file": str(CANDIDATES.relative_to(ROOT)),
            "candidate_file_sha256": digest(CANDIDATES),
            "observation_file": str(OBSERVATIONS.relative_to(ROOT)),
            "observation_file_sha256": digest(OBSERVATIONS),
            "observation_method": "Playwright Chromium, fresh anonymous context per ranked domain, initial root-page load only",
        },
        "count_reconciliation": candidates["count_reconciliation"],
        "selection_policy": {
            "rank_order_preserved": True,
            "existing_site_domains_excluded": True,
            "login_blank_error_captcha_and_duplicate_final_pages_excluded": True,
            "adult_domains_excluded_upstream_without_visit": True,
            "no_content_page_substitution": True,
            "selected_count_required": 40,
            "target_unique_site_domain_count": 100,
        },
        "selected_count": len(selected),
        "ineligible_count": len(ineligible),
        "reserve_count": len(reserves),
        "cases": selected,
        "ineligible": ineligible,
        "reserves": reserves,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    lines = [
        "# 中国榜热门网站首页扩充：选站人工审查",
        "",
        "> 本文件由 `scripts/build_china_homepage_selection.py` 从冻结的 Semrush 中国 Top 100、现有 52 个首页案例、旧报告 10 个热门网站案例和独立 Chromium 根页观察机械生成。排名只用于选样，不参与得分。",
        "",
        "## 计数核对",
        "",
        f"- 现有首页案例：{result['count_reconciliation']['homepage_case_count']}。",
        f"- 旧热门网站案例：{result['count_reconciliation']['legacy_popular_case_count']}。",
        f"- 明确重复网站：{', '.join(result['count_reconciliation']['definite_overlapping_site_domains'])}。",
        f"- 现有唯一网站域名：{result['count_reconciliation']['unique_existing_site_domain_count']}。",
        f"- 严格补足到 100 个唯一网站需新增：{result['count_reconciliation']['new_unique_site_domains_required']}。",
        "",
        "## 拟新增 40 个网站",
        "",
        "| 中国榜名次 | 网站 | URL | 标签 | 首次加载结果 | 页面标题 |",
        "|---:|---|---|---|---|---|",
    ]
    for case in selected:
        evidence = case["evidence"]
        title = (evidence["title"] or "—").replace("|", "\\|")
        lines.append(
            f"| {case['china_rank']} | `{case['domain']}` | {case['url']} | `{case['label']}` | "
            f"HTTP {evidence['http_status']}；{evidence['visible_text_length']} 字符 | "
            f"{title} |"
        )
    lines.extend(
        [
            "",
            "## 标签口径",
            "",
            "- `landing_page`：公开品牌、产品、服务或搜索入口页，按稳定页面身份与功能文字验收。",
            "- `content_list`：首页直接呈现多条公开内容或栏目，规则可要求稳定文字和至少 3 条具名详情入口。",
            "- `product_catalog`：首页直接呈现商品或供应目录，不冻结当前商品、价格、排名或顺序。",
            "- 本轮没有为了凑标签而把首页替换成文章或正文详情页；`article`、`search_results`、`body_detail` 因实际入选根页形态不适用。",
            "",
            "## 不合格候选",
            "",
            "| 名次 | 网站 | 原因码 | 证据摘要 |",
            "|---:|---|---|---|",
        ]
    )
    for item in ineligible:
        lines.append(
            f"| {item['china_rank']} | `{item['domain']}` | `{item['reason_code']}` | {item['reason']} |"
        )
    lines.extend(
        [
            "",
            "## 合格替补",
            "",
            "下列页面可访问，但在名次更高的候选已补足 40 个后不进入本轮主集：",
            "",
            "| 名次 | 网站 | 页面标题 |",
            "|---:|---|---|",
        ]
    )
    for item in reserves:
        title = (item["evidence"].get("title") or "—").replace("|", "\\|")
        lines.append(f"| {item['china_rank']} | `{item['domain']}` | {title} |")
    lines.extend(
        [
            "",
            "## 人工复核栏",
            "",
            "- [ ] 同意将‘最终 100 个且不重复’解释为 60 个现有唯一域名 + 40 个新增域名。",
            "- [ ] 同意以上 40 个网站及标签。",
            "- [ ] 同意排除登录页、空白/错误页、重复最终页和只见同意层的页面。",
            "- [ ] 同意规则只取首次直接加载证据，不点击、不递归，不冻结动态条目、价格和排行。",
            "",
        ]
    )
    REVIEW.write_text("\n".join(lines), encoding="utf-8")
    print(f"Selected {len(selected)}; ineligible {len(ineligible)}; reserves {len(reserves)}")


if __name__ == "__main__":
    main()
