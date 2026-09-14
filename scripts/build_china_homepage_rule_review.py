#!/usr/bin/env python3
# ruff: noqa: E501 -- generated Chinese review prose is intentionally kept whole
"""Build draft China-homepage acceptance rules for human review.

The output is intentionally not an executable dataset.  It freezes proposed
contracts against independent browser evidence, then stops at the user's review
gate before any provider call or current-report change.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from harness.web_fetch.dynamic_lists import extract_named_entries
from harness.web_fetch.stable_tasks import validate_acceptance_contract

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "research/china-homepage-selection.json"
CAPTURE = ROOT / "research/china-homepage-rule-evidence-2026-09-11.json"
RULES_OUTPUT = ROOT / "research/china-homepage-rules.json"
REVIEW_OUTPUT = ROOT / "research/china-homepage-rules-review.md"


def identity(*anchors: str | tuple[str, ...], note: str = "") -> dict:
    return {"anchors": groups(anchors), "note": note}


def listing(
    anchors: tuple[str | tuple[str, ...], ...],
    hosts: tuple[str, ...],
    entry_path: str,
    note: str = "",
) -> dict:
    return {
        "anchors": groups(anchors),
        "hosts": list(hosts),
        "entry_path": entry_path,
        "note": note,
    }


def groups(anchors) -> list[list[str]]:
    return [list(anchor) if isinstance(anchor, tuple) else [anchor] for anchor in anchors]


RULES = {
    "doubao.com": identity("豆包", "有什么我能帮你的吗？", "对话", "图像生成"),
    "feishu.cn": identity("飞书", "字节跳动旗下 AI 工作平台", "智能会议纪要", "多维表格"),
    "163.com": listing(
        ("网易", "新闻", "体育", "财经"), ("www.163.com",), r"/dy/article/[A-Z0-9]+\.html"
    ),
    "taobao.com": identity(
        "淘宝",
        "搜索",
        "购物车",
        "收藏夹",
        note="商品 ID 位于 item.htm 的查询参数中，共享提取器按主机与路径去重；暂按目录身份核验。",
    ),
    "aliyun.com": identity("阿里云", "大模型", "云市场", "控制台", "精选产品"),
    "weibo.com": identity(
        "微博",
        "热门推荐",
        "热门榜单",
        "热搜",
        note="观察到公开内容流，但文章链接的可见标签主要是时间而非标题，故不把它们伪装成具名记录。",
    ),
    "360.com": identity("360安全卫士", "软件下载中心", "纳米AI", "360安全浏览器"),
    "dingtalk.com": identity("钉钉", "AI钉钉", "产品功能", "开放平台", "下载钉钉"),
    "hao123.com": identity(
        "hao123",
        "上网从这里开始",
        "搜索热点",
        "热点",
        note="新闻链接以查询参数承载身份，当前共享提取器会按主机与路径去重；暂按页面身份核验。",
    ),
    "1688.com": identity(
        "阿里1688首页",
        "挑好货",
        "找工厂",
        "工业品",
        note="商品分类链接共用 /selloffer/offer_search.htm，身份在查询参数中；暂按目录身份核验。",
    ),
    "quark.cn": identity("夸克", "AI浏览器", "AI搜索", "夸克网盘"),
    "kdocs.cn": identity("金山文档", "WPS云文档", "智能文档", "多人协作"),
    "kimi.com": identity("Kimi", "New Chat", "Deep Research", "Kimi Code"),
    "tmall.com": identity(
        "天猫Tmall.com",
        "搜索",
        "购物车",
        "商家支持",
        note="商品 ID 位于 item.htm 的查询参数中，共享提取器按主机与路径去重；暂按目录身份核验。",
    ),
    "wedoany.com": listing(
        ("维度网", "工程分类", "产品", "选型总库"), ("www.wedoany.com",), r"/product/[0-9]+\.html"
    ),
    "qianwen.com": identity("千问", "阿里旗下全能AI助手", "新建对话", "千问创作"),
    "douyu.com": listing(("斗鱼", "直播", "分类", "视频"), ("www.douyu.com",), r"/[0-9]+"),
    "openai.com": identity("OpenAI", "Research", "Products", "Developers", "Try ChatGPT"),
    "alipay.com": identity("支付宝", "我是服务商", "我是开发者", "我是商家用户"),
    "sohu.com": listing(("搜狐", "新闻", "财经", "体育"), ("www.sohu.com",), r"/a/[0-9]+_[0-9]+"),
    "huya.com": listing(
        ("虎牙直播", "绝地求生", "英雄联盟", "王者荣耀"), ("www.huya.com",), r"/video/u/[0-9]+"
    ),
    "cnki.net": identity(
        "中国知网",
        "学术期刊",
        "学位论文",
        "下载热榜",
        note="文献身份位于查询参数，而共享提取器按主机与路径去重；规则不冻结当前论文标题。",
    ),
    "nih.gov": identity(
        "National Institutes of Health",
        "Health Information",
        "Grants & Funding",
        "NIH at a Glance",
        note="首页混合专题、跨 NIH 机构资源与栏目入口；没有单一、稳定的详情 URL 形状。",
    ),
    "huaban.com": listing(
        ("花瓣网", "探索", "平面设计", "UI/UX"), ("huaban.com",), r"/pins/[0-9]+"
    ),
    "jianying.com": identity("剪映AI", "视频生成", "专业剪辑", "智能抠像"),
    "eastmoney.com": listing(
        ("东方财富网", "财经", "股票", "基金"), ("finance.eastmoney.com",), r"/a/[0-9]+\.html"
    ),
    "sina.com.cn": listing(
        ("新浪网", "新闻", "财经", "体育"),
        ("news.sina.com.cn", "finance.sina.com.cn", "sports.sina.com.cn"),
        r"/.*/[0-9]{4}-[0-9]{2}-[0-9]{2}/[^/]+\.shtml",
    ),
    "gamer520.com": listing(
        ("Switch520", "PC游戏", "IGN必玩推荐榜"), ("www.gamer520.com",), r"/[0-9]+\.html"
    ),
    "lingxing.com": identity("领星ERP", "核心功能", "进销存一体管理", "财务精准管理"),
    "alibaba.com": listing(
        ("Alibaba.com", "Discover manufacturers", "Request for Quotation", "Top Ranking"),
        ("www.alibaba.com",),
        r"/product-detail/.+",
    ),
    "youdao.com": identity("网易有道", "词典", "翻译", "有道龙虾"),
    "3dmgame.com": listing(
        ("单机游戏", "新闻", "专栏", "硬件"), ("www.3dmgame.com",), r"/games/[A-Za-z0-9_-]+"
    ),
    "2345.com": listing(
        ("2345网址导航", "旗下产品", "邮箱", "天气"),
        ("news.jxcn.cn", "news-pc.gzylhy.cn"),
        r"/p/[0-9]{8}/[0-9]+\.html",
        note="具名内容来自首页直接公开的资讯卡片；第三方承载主机可能变化，冻结前应再复查。",
    ),
    "cctv.com": listing(
        ("央视网", "新闻", "财经", "体育"),
        ("news.cctv.com", "tv.cctv.com"),
        r"/[0-9]{4}/[0-9]{2}/[0-9]{2}/(?:ARTI|VIDE)[A-Za-z0-9]+\.shtml",
    ),
    "so.com": identity("360搜索", "搜全网", "问AI", "AI写作"),
    "volcengine.com": identity("火山引擎", "火山方舟", "精选产品", "控制台"),
    "nga.cn": identity(
        "NGA玩家社区",
        "论坛",
        "评分",
        "专题",
        note="帖子身份在 read.php 查询参数中，共享提取器会合并为一个路径；暂按页面身份核验。",
    ),
    "yandex.com": identity("Yandex", "Search with Yandex AI", "Maps"),
    "elsevier.com": identity("Elsevier", "Academic & Government", "Health", "Publish with us"),
    "huawei.com": identity("Huawei", "Enterprise", "Consumer", "Huawei Cloud"),
}


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def evidence_text(observation: dict) -> str:
    values = [observation.get("title", ""), observation.get("visible_text", "")]
    values.extend(item.get("text", "") for item in observation.get("headings", []))
    values.extend(item.get("label", "") for item in observation.get("controls", []))
    values.extend(item.get("text", "") for item in observation.get("links", []))
    return normalized("\n".join(values))


def acceptance(rule: dict) -> dict:
    anchors = [
        {"id": f"stable-anchor-{index}", "alternatives": values}
        for index, values in enumerate(rule["anchors"], 1)
    ]
    if "hosts" not in rule:
        contract = {
            "version": 1,
            "schema": "page_identity",
            "minimum_records": 0,
            "stable_text_anchors": anchors,
        }
    else:
        for anchor in anchors:
            anchor["match_scope"] = "non_record_lines"
        contract = {
            "version": 1,
            "schema": "named_link",
            "minimum_records": 3,
            "hosts": rule["hosts"],
            "entry_path": rule["entry_path"],
            "stable_text_anchors": anchors,
        }
    validate_acceptance_contract(contract)
    return contract


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    selection = json.loads(SELECTION.read_text())
    capture = json.loads(CAPTURE.read_text())
    cases = {item["domain"]: item for item in selection["cases"]}
    observations = {item["domain"]: item for item in capture["observations"]}
    if set(RULES) != set(cases) or set(observations) != set(cases):
        raise SystemExit("Rule, selection and capture domains must match exactly")

    machine_rules = []
    for selected in selection["cases"]:
        domain = selected["domain"]
        observation = observations[domain]
        if observation.get("observation_state") != "captured" or not observation.get(
            "visible_text_length"
        ):
            raise ValueError(f"{domain}: independent evidence is not a non-empty captured page")
        rule = RULES[domain]
        haystack = evidence_text(observation)
        for index, alternatives in enumerate(rule["anchors"], 1):
            if not any(normalized(value) in haystack for value in alternatives):
                raise ValueError(
                    f"{domain}: stable anchor group {index} absent from evidence: {alternatives}"
                )
        contract = acceptance(rule)
        links = [{"title": item["text"], "url": item["url"]} for item in observation["links"]]
        entries = (
            extract_named_entries("", contract, observation["final_url"], links=links)
            if contract["schema"] == "named_link"
            else []
        )
        if contract["schema"] == "named_link" and len(entries) < contract["minimum_records"]:
            raise ValueError(
                f"{domain}: only {len(entries)} independently observed named links match"
            )
        machine_rules.append(
            {
                "id": selected["id"],
                "china_rank": selected["china_rank"],
                "domain": domain,
                "url": selected["url"],
                "observed_final_url": observation["final_url"],
                "label": selected["label"],
                "rule_model": contract["schema"],
                "acceptance_contract": contract,
                "observed_matching_named_link_count": len(entries),
                "observed_named_link_examples": entries[:3],
                "rule_rationale": rule.get("note")
                or (
                    "首页存在至少 3 个符合单一稳定 URL 结构的具名条目。"
                    if entries
                    else "按稳定页面身份与主要功能文字验收。"
                ),
                "evidence": {
                    "capture_file": str(CAPTURE.relative_to(ROOT)),
                    "capture_file_sha256": sha256(CAPTURE),
                    "visible_text_sha256": observation["visible_text_sha256"],
                    "checked_at": "2026-09-11",
                    "provider_output_used": False,
                    "clicks_or_recursive_navigation": False,
                },
                "volatile_exclusions": [
                    "current_items",
                    "ordering",
                    "rank",
                    "price",
                    "count",
                    "date",
                ],
                "review_status": "draft_pending_human_review",
            }
        )

    label_counts = Counter(item["label"] for item in machine_rules)
    model_counts = Counter(item["rule_model"] for item in machine_rules)
    payload = {
        "schema_version": 1,
        "purpose": "Human-review draft of per-site rules for the 40 China-ranked homepage additions",
        "status": "draft_pending_human_review",
        "review_gate": "Do not generate the executable combined dataset or call lexmount-global before user approval",
        "source_selection": str(SELECTION.relative_to(ROOT)),
        "source_selection_sha256": sha256(SELECTION),
        "source_capture": str(CAPTURE.relative_to(ROOT)),
        "source_capture_sha256": sha256(CAPTURE),
        "common": {
            "all_registered_anchor_groups_required": True,
            "named_link_minimum_records": 3,
            "recursive_navigation_allowed": False,
            "provider_output_used_for_rules": False,
            "dynamic_members_and_values_frozen": False,
        },
        "label_counts": dict(sorted(label_counts.items())),
        "rule_model_counts": dict(sorted(model_counts.items())),
        "cases": machine_rules,
    }
    RULES_OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_review(payload)
    print(
        f"Built {len(machine_rules)} draft rules: "
        f"named_link={model_counts['named_link']}, page_identity={model_counts['page_identity']}"
    )


def write_review(payload: dict) -> None:
    lines = [
        "# 中国榜热门网站首页扩充：规则人工审查",
        "",
        "> 这是人工确认前的规则草案，由独立 Chromium 首次加载证据机械校验。没有调用 `lexmount-global`，没有生成供应商结果，也没有修改当前报告矩阵。",
        "",
        "## 规则口径",
        "",
        "| 页面标签 | 默认验收方式 | 本轮处理 |",
        "|---|---|---|",
        "| `landing_page` | 页面身份 | 全部稳定身份/主要功能文字组都要命中；不要求虚构列表。 |",
        "| `content_list` | 优先具名链接 | URL 结构可靠时要求至少 3 条不同具名入口；身份藏在查询参数或链接不具名时，改用页面身份规则。 |",
        "| `product_catalog` | 优先具名商品链接 | 有独立商品详情 URL 时要求至少 3 条；只有共享分类路径时，改用目录身份规则。 |",
        "| `article` / `body_detail` | 正文覆盖 | 本轮严格保留榜单根页，没有此类入选页；不为凑标签改抓文章。 |",
        "| `search_results` | 查询身份 + 结果结构 | 本轮没有以带查询词的结果页替换根页，因此没有此类入选页。 |",
        "",
        "所有稳定文字组均要求命中，但组内候选词只需命中一个。动态条目、顺序、榜次、价格、数量和日期不固定，也不参与事实正确性判断。",
        "",
        "## 汇总",
        "",
        f"- 规则总数：{len(payload['cases'])}。",
        f"- 标签：{json.dumps(payload['label_counts'], ensure_ascii=False, sort_keys=True)}。",
        f"- 模型：{json.dumps(payload['rule_model_counts'], ensure_ascii=False, sort_keys=True)}。",
        "- 机器可读规则：`research/china-homepage-rules.json`。",
        "",
        "## 逐站规则",
        "",
        "| 排名 | 网站 | 标签 | 模型 | 稳定文字组 | 证据中匹配链接数 |",
        "|---:|---|---|---|---|---:|",
    ]
    for item in payload["cases"]:
        anchors = "；".join(
            " / ".join(a["alternatives"])
            for a in item["acceptance_contract"]["stable_text_anchors"]
        )
        anchors_cell = anchors.replace("|", "\\|")
        lines.append(
            f"| {item['china_rank']} | `{item['domain']}` | `{item['label']}` | `{item['rule_model']}` | "
            f"{anchors_cell} | {item['observed_matching_named_link_count']} |"
        )
    lines.extend(["", "## 逐站详情", ""])
    for item in payload["cases"]:
        contract = item["acceptance_contract"]
        lines.extend(
            [
                f"### {item['china_rank']}. `{item['domain']}`",
                "",
                f"- 请求 URL：{item['url']}",
                f"- 首次加载最终 URL：{item['observed_final_url']}",
                f"- 标签 / 模型：`{item['label']}` / `{item['rule_model']}`",
                f"- 规则理由：{item['rule_rationale']}",
                "- 稳定文字："
                + "；".join(" / ".join(a["alternatives"]) for a in contract["stable_text_anchors"]),
            ]
        )
        if contract["schema"] == "named_link":
            lines.extend(
                [
                    f"- 链接契约：主机 `{', '.join(contract['hosts'])}`；路径正则 `{contract['entry_path']}`；至少 {contract['minimum_records']} 条。",
                    f"- 独立证据匹配：{item['observed_matching_named_link_count']} 条。以下仅是观察样例，不冻结标题或成员：",
                ]
            )
            for example in item["observed_named_link_examples"]:
                title = example["title"].replace("\n", " ").strip()
                lines.append(f"  - {title} — {example['url']}")
        lines.extend(["- [ ] 规则同意", ""])
    lines.extend(
        [
            "## 总体人工确认",
            "",
            "- [ ] 同意 40 个新增网站的标签。",
            "- [ ] 同意逐站稳定文字组和具名链接路径契约。",
            "- [ ] 同意动态成员和值不作为固定真值。",
            "- [ ] 确认后再生成合并后的 100 站可执行测试集，并运行 `lexmount-global`。",
            "",
        ]
    )
    REVIEW_OUTPUT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
