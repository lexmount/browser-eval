#!/usr/bin/env python3
# ruff: noqa: E501, B905
"""Build the ranked-domain homepage suite from final browser evidence.

All literals below are reviewed semantic selections from the bound independent
Chromium capture.  The builder fails closed if an anchor is absent or a list
contract does not have three matching named links in that same capture.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

from harness.web_fetch.config import load_dataset
from harness.web_fetch.dynamic_lists import extract_named_entries
from harness.web_fetch.stable_tasks import validate_acceptance_contract

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "research/global-homepage-final-selection.json"
CAPTURE = ROOT / "research/global-homepage-rule-evidence-final-v2-2026-09-11.json"
VISUAL_SPOT_CHECK = ROOT / "research/global-homepage-visual-spot-check-2026-09-11.json"
VARIANT_REVIEW = ROOT / "research/global-homepage-rule-variant-review-2026-09-11.json"
LOCALE_REVIEW = ROOT / "research/global-homepage-fixed-locale-observations-2026-09-11.json"
SELECTED_OUTPUT = ROOT / "research/global-homepage-selected-pages.json"
RULES_OUTPUT = ROOT / "research/global-homepage-rules.json"
REVIEW_OUTPUT = ROOT / "research/global-homepage-rules-review.md"
DATASET_OUTPUT = ROOT / "evaluation/global-homepages-dataset.json"


def groups(anchors) -> list[list[str]]:
    return [list(anchor) if isinstance(anchor, (tuple, list)) else [anchor] for anchor in anchors]


def identity(*anchors) -> dict:
    return {"anchors": groups(anchors)}


def listing(anchors: tuple, hosts: tuple[str, ...], entry_path: str) -> dict:
    return {
        "anchors": groups(anchors),
        "hosts": list(hosts),
        "entry_path": entry_path,
    }


RULES = {
    "google.com": identity(
        "Google", "About", "Advertising", "Business", "Privacy", "Terms",
    ),
    # The public root can show either a populated recommendation feed or an
    # anonymous empty-state shell.  Current video membership is deliberately not
    # frozen, so only the stable product identity is required.
    "youtube.com": identity("YouTube Home", "Guide", "Search", "Settings", "Sign in"),
    "chatgpt.com": identity(
        "ChatGPT",
        "Where should we begin?", "Chat with ChatGPT", "New chat",
    ),
    "reddit.com": listing(("POPULAR COMMUNITIES", "Best", "Hot"),
        ("www.reddit.com", "reddit.com"), r"/r/[^/]+/comments/[a-z0-9]+(?:/[^/?#]+)?"),
    "whatsapp.com": identity("Message privately", "Never miss a moment with voice and video calls",
        "Keep in touch with your groups", "Transform your business"),
    "bing.com": identity(
        "Microsoft Bing", "Images", "Maps", "Sign in",
    ),
    "wikipedia.org": identity("Wikipedia", "The Free Encyclopedia", "English", "日本語"),
    "gemini.google.com": identity(
        "Gemini", "Conversation with Gemini", "Meet Gemini, your personal AI assistant",
    ),
    "yahoo.co.jp": listing(("Yahoo! JAPAN", "主なサービス", "お知らせ", "ニュース"),
        ("news.yahoo.co.jp",), r"/pickup/[0-9]+"),
    "yahoo.com": listing(("Search the web", "News", "Finance", "Sports", "Trending"),
        ("www.yahoo.com", "hk.news.yahoo.com", "hk.finance.yahoo.com"),
        r"/(?:news|entertainment|finance)/.+"),
    "amazon.com": listing(("Amazon", "Today's Deals", "Customer Service"),
        ("www.amazon.com",), r"(?:/[^/]+)?/dp/[A-Z0-9]{10}"),
    "baidu.com": identity("百度一下", "新闻", "地图", "更多"),
    "naver.com": listing(("NAVER", "공지사항", "Partners", "Developers"),
        ("news.naver.com", "blog.naver.com", "cafe.naver.com", "in.naver.com", "m.naver.com"), r"/.+"),
    "netflix.com": identity("Unlimited movies, TV shows, and more", "More Reasons to Join",
        "Frequently Asked Questions"),
    "cloud.microsoft": identity("Work smarter across Microsoft 365 with Copilot", "Popular prompts to try",
        "Jump into your favorite app"),
    "bilibili.com": listing(("首页", "番剧", "直播", "热门"),
        ("www.bilibili.com",), r"/video/(?:BV[A-Za-z0-9]+|av[0-9]+)"),
    "dzen.ru": listing(("Поиск Яндекса", "Новости", "Статьи", "Видео"),
        ("dzen.ru",), r"/(?:a/[A-Za-z0-9_-]+|news/story/[A-Za-z0-9_-]+)"),
    "microsoft.com": identity("Microsoft", "Microsoft 365", "Windows", "Surface", "XBOX"),
    "twitch.tv": listing(("Live Channels", "Live on Twitch", "Categories we think you’ll like"),
        ("www.twitch.tv",),
        r"/(?!directory(?:/|$)|downloads?$|jobs?$|settings|login|signup|search|subscriptions|wallet|turbo|videos|friends|inventory|store|drops|creatorcamp|prime|about|legal|brand|security|partners|press|advertisers|blog)[A-Za-z0-9_]+"),
    "weather.com": identity("Forecasts", "Radar", "Today's Outlook"),
    "canva.com": identity(
        "Canva home", "What will you design today?", "Start designing",
        "Canva AI",
    ),
    "news.yahoo.co.jp": listing(("Yahoo!ニュース", "トピックス", "アクセスランキング"),
        ("news.yahoo.co.jp",), r"/pickup/[0-9]+"),
    "fandom.com": listing(("Fandom", "GAMES", "MOVIES", "TV", "EXPLORE PROPERTIES"),
        ("www.fandom.com",), r"/topics/(?:games|movies|tv)"),
    "globo.com": listing(("globo.com", "Guia de Compras", "Previsão do tempo"),
        ("g1.globo.com",), r"/.+/(?:noticia|blog)/.+"),
    "mail.ru": listing(("Mail.ru", "Почта", "Облако", "Календарь"),
        ("news.mail.ru",), r"/[^/]+/[0-9]+"),
    "samsung.com": identity("Samsung", "Mobile", "TV & AV", "Appliances", "Computers & Monitors"),
    "duckduckgo.com": identity("DuckDuckGo", "Search", "Duck.ai", "Settings", "Privacy Policy"),
    "discord.com": identity("GROUP CHAT THAT’S ALL FUN & GAMES", "MAKE YOUR GROUP CHATS MORE FUN",
        "STREAM LIKE YOU’RE IN THE SAME ROOM", "HOP IN WHEN YOU'RE FREE, NO NEED TO CALL"),
    "github.com": identity("GitHub features", "GitHub Copilot", "GitHub Actions", "GitHub Codespaces"),
    "spotify.com": identity("Your Library", "Trending songs", "Popular artists", "Popular albums and singles"),
    "nytimes.com": listing(("The New York Times", "TOP STORIES", "NEWSLETTERS", "PODCASTS"),
        ("www.nytimes.com",), r"/[0-9]{4}/[0-9]{2}/[0-9]{2}/.+"),
    "zoom.us": identity("Find out what's possible when work connects",
        "One platform. Endless ways to work together.", "Trusted by millions. Built for you.",
        "Businesses achieve more with Zoom"),
    "booking.com": identity("Booking.com", "Stays", "Flights", "Car rental", "Search"),
    "brave.com": identity("The browser that puts you first", "Private search", "Powerful VPN",
        "A Web with fewer ads"),
    "roblox.com": identity("One platform. Millions of games.", "Games to get you started",
        "Simple controls, maximum fun", "Get started with Roblox today"),
    "apple.com": identity("Apple", "Mac", "iPhone", "Watch"),
    "espn.com": listing(("ESPN", "Quick Links", "Top Headlines"),
        ("www.espn.com",), r"/[a-z0-9-]+/story/_/id/[0-9]+/.+"),
    "bbc.com": listing(("BBC", "News", "Sport", "Business", "Technology"),
        ("www.bbc.com",), r"/news/articles/[a-z0-9]+"),
    "paypal.com": identity("PayPal", "Personal", "Business", "Get the PayPal app"),
    "ya.ru": identity("Запрос", "Поиск по картинке", "Сервисы"),
    "deepseek.com": identity("Into the Unknown", "DeepSeek Web", "API Platform", "API Docs"),
    "indeed.com": identity("Your next job starts here", "Trending Searches", "Trending Jobs", "Search"),
    "rakuten.co.jp": listing(("ようこそ楽天市場へ", "楽天スーパーDEAL", "人気商品ランキング", "ジャンル"),
        ("item.rakuten.co.jp",), r"/.+"),
    "telegram.org": identity("Why Telegram?", "Simple", "Private", "Secure", "Powerful"),
    "cnn.com": listing(("CNN", "MORE TOP STORIES"),
        ("www.cnn.com", "edition.cnn.com"), r"/[0-9]{4}/[0-9]{2}/[0-9]{2}/.+"),
    "namu.wiki": identity("나무위키에 오신 것을 환영합니다!", "나무위키는 누구나 기여할 수 있는 위키입니다.",
        "검증되지 않았거나 편향된 내용이 있을 수 있습니다.", "나무위키의 도움말"),
    "shein.com": identity("SHEIN", "Women Clothing", "Home & Living", "Electronics"),
    "qq.com": listing(("产品推荐", "热点精选", "腾讯新闻出品", "热门问答"),
        ("news.qq.com",), r"/rain/a/[A-Za-z0-9]+"),
    "douyin.com": identity("精选", "推荐", "放映厅", "短剧"),
    "usps.com": identity("Quick Tools", "Track a Package", "Find USPS Locations", "Buy Stamps"),
    "instructure.com": identity("Instructure", "Canvas", "Explore Canvas", "Why Instructure"),
    "primevideo.com": identity("Prime Video", "电影", "电视节目", "体育"),
    "rutube.ru": listing(("RUTUBE", "Для вас", "Shorts", "ТВ-каналы", "Рекомендации"),
        ("rutube.ru",), r"/video/[a-f0-9]+"),
}

LABEL_OVERRIDES = {
    "shein.com": ("landing_page", "首页以品类导航和品牌入口为主，没有三个可验证的商品详情链接。"),
    "douyin.com": ("entry_page", "页面呈现公开应用导航和视频流，但没有逐条可提取详情链接；按入口页核验。"),
}

BLOCKED_TEXT = [
    "Access Denied", "Human Verification", "Verify that you are human",
    "Robot or human?", "Checking your browser", "Not Found",
]


def key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def evidence_text(observation: dict) -> str:
    values = [observation.get("title", ""), observation.get("visible_text", "")]
    values.extend(item.get("text", "") for item in observation.get("headings", []))
    values.extend(item.get("label", "") for item in observation.get("controls", []))
    values.extend(item.get("text", "") for item in observation.get("links", []))
    return key("\n".join(values))


def locale_observation(item: dict) -> dict:
    """Project a reviewed visible-browser observation into the capture shape."""
    visible_text = "\n".join(group[0] for group in item["stable_text_groups"])
    return {
        "observation_state": "captured",
        "final_url": item["observed_final_url"],
        "title": item["stable_text_groups"][0][0],
        "visible_text": visible_text,
        "visible_text_length": len(visible_text),
        "visible_text_sha256": hashlib.sha256(visible_text.encode()).hexdigest(),
        "headings": [],
        "controls": [],
        "links": item.get("observed_links", []),
        "http_status": None,
        "navigation_error": None,
    }


def acceptance(case: dict, rule: dict) -> dict:
    anchors = [
        {"id": f"stable-anchor-{index}", "alternatives": alternatives}
        for index, alternatives in enumerate(rule["anchors"], 1)
    ]
    if "hosts" in rule:
        for anchor in anchors:
            anchor["match_scope"] = "non_record_lines"
        contract = {
            "version": 1, "schema": "named_link", "minimum_records": 3,
            "hosts": rule["hosts"], "entry_path": rule["entry_path"],
            "stable_text_anchors": anchors,
        }
    else:
        contract = {
            "version": 1, "schema": "page_identity", "minimum_records": 0,
            "stable_text_anchors": anchors,
        }
    validate_acceptance_contract(contract)
    return contract


def write_evidence(
    case: dict,
    observation: dict,
    rule: dict,
    evidence_file: Path,
    evidence_file_hash: str,
    evidence_source: str,
) -> tuple[str, str, int]:
    relative = Path("sources") / case["id"] / "stable-page.txt"
    target = ROOT / "evaluation" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n\n".join(" / ".join(group) for group in rule["anchors"]) + "\n", encoding="utf-8")
    stable_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    contract = acceptance(case, rule)
    observed_links = [{"title": item["text"], "url": item["url"]} for item in observation["links"]]
    entries = extract_named_entries("", contract, observation["final_url"], links=observed_links) \
        if contract["schema"] == "named_link" else []
    if contract["schema"] == "named_link" and len(entries) < 3:
        raise ValueError(f"{case['domain']}: only {len(entries)} independently observed named links match")
    record = {
        "case_id": case["id"], "domain": case["domain"], "url": case["url"],
        "final_url": observation["final_url"], "checked_at": "2026-09-11",
        "source": evidence_source,
        "capture_file": str(evidence_file.relative_to(ROOT)),
        "capture_file_sha256": evidence_file_hash,
        "visible_text_sha256": observation["visible_text_sha256"],
        "stable_page_sha256": stable_hash,
        "anchor_groups": rule["anchors"], "matching_named_link_count": len(entries),
        "clicks_or_recursive_navigation": False, "provider_output_used": False,
        "scope": "Task-relevant stable page text plus declared list structure; dynamic members and values are not frozen",
    }
    target.with_name("evidence.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return relative.as_posix(), stable_hash, len(entries)


def prompt(case: dict, contract: dict) -> str:
    texts = "；".join(" / ".join(group) for group in RULES[case["domain"]]["anchors"])
    if contract["schema"] == "named_link":
        return (f"直接获取这个热门域名首页，返回至少 3 条不同的具名详情入口，链接符合登记的主机和路径；"
                f"同时保留这些稳定页面文字：{texts}。不点击、不递归，不固定当前条目、顺序、价格、排名或数量。")
    return (f"直接获取这个热门域名首页，并保留这些稳定页面身份或主要功能文字：{texts}。"
            "不点击、不递归，不要求长正文或三个详情链接。")


def main() -> None:
    selection_raw = SELECTION.read_bytes()
    capture_raw = CAPTURE.read_bytes()
    visual_spot_check_raw = VISUAL_SPOT_CHECK.read_bytes()
    variant_review_raw = VARIANT_REVIEW.read_bytes()
    locale_review_raw = LOCALE_REVIEW.read_bytes()
    selection = json.loads(selection_raw)
    observations = {item["domain"]: item for item in json.loads(capture_raw)["observations"]}
    locale_review_payload = json.loads(locale_review_raw)
    locale_reviews = {item["case_id"]: item for item in locale_review_payload["cases"]}
    locale_contract_reviews = {
        case_id: item for case_id, item in locale_reviews.items()
        if item["decision"] in {"change_url_and_contract", "refresh_contract_same_url"}
    }
    locale_url_changes = {
        case_id: item for case_id, item in locale_reviews.items()
        if item["decision"] == "change_url_and_contract"
    }
    deferred_cases = {
        case_id: item for case_id, item in locale_reviews.items()
        if item["decision"] == "defer_no_deterministic_locale_contract"
    }
    expected_url_changes = {
        "global-homepage-001-google-com",
        "global-homepage-002-youtube-com",
        "global-homepage-015-yahoo-com",
        "global-homepage-029-twitch-tv",
        "global-homepage-039-duckduckgo-com",
        "global-homepage-044-spotify-com",
        "global-homepage-047-booking-com",
        "global-homepage-067-deepseek-com",
        "global-homepage-082-shein-com",
        "global-homepage-088-primevideo-com",
    }
    if set(locale_url_changes) != expected_url_changes:
        raise SystemExit("Locale review must change URLs only for independently proven variants")
    if set(locale_contract_reviews) - set(locale_url_changes) != {"global-homepage-010-bing-com"}:
        raise SystemExit("Only Bing has a same-URL contract refresh in this audit")
    if set(deferred_cases) != {"global-homepage-052-roblox-com"}:
        raise SystemExit("Only Roblox is deferred for lack of a deterministic locale contract")
    visual_spot_check_payload = json.loads(visual_spot_check_raw)
    visual_spot_checks = {
        item["domain"]: item for item in visual_spot_check_payload["checks"]
        if item.get("state") == "observed"
    }
    youtube_visual = visual_spot_checks.get("youtube.com")
    if not youtube_visual or youtube_visual.get("page_form") != "content_list":
        raise SystemExit("Visible-browser audit must support the YouTube content-list correction")
    cases_by_domain = {case["domain"]: case for case in selection["cases"]}
    if set(RULES) != set(cases_by_domain) or set(observations) != set(cases_by_domain):
        raise SystemExit("Rules, final selection and final capture must cover the same domains")
    capture_hash = hashlib.sha256(capture_raw).hexdigest()
    visual_spot_check_hash = hashlib.sha256(visual_spot_check_raw).hexdigest()
    variant_review_hash = hashlib.sha256(variant_review_raw).hexdigest()
    locale_review_hash = hashlib.sha256(locale_review_raw).hexdigest()
    finalized, machine_rules, dataset_cases = [], [], []
    deferred_exclusions = []
    for original in selection["cases"]:
        domain = original["domain"]
        deferred_item = deferred_cases.get(original["id"])
        if deferred_item:
            deferred_exclusions.append({
                "domain": domain,
                "url": original["url"],
                "reason_code": "locale_contract_not_deterministic",
                "reason": deferred_item["note"],
                "evidence": {
                    "requested_url": deferred_item["requested_url"],
                    "observed_final_url": deferred_item["observed_final_url"],
                    "source": str(LOCALE_REVIEW.relative_to(ROOT)),
                },
                "excluded_at": "final_locale_contract_audit",
            })
            continue
        observation = observations[domain]
        evidence_file = CAPTURE
        evidence_file_hash = capture_hash
        evidence_source = "Playwright Chromium, fresh anonymous context, initial ranked-domain page only"
        locale_item = locale_contract_reviews.get(original["id"])
        if locale_item:
            if not all(
                group in locale_item["stable_text_groups"]
                for group in RULES[domain]["anchors"]
            ):
                raise ValueError(
                    f"{domain}: fixed-locale rules add text absent from independent observation"
                )
            if locale_item["decision"] == "change_url_and_contract":
                original = {**original, "url": locale_item["requested_url"]}
            observation = locale_observation(locale_item)
            evidence_file = LOCALE_REVIEW
            evidence_file_hash = locale_review_hash
            evidence_source = "Codex in-app Chromium, independently reviewed homepage contract"
        if observation.get("observation_state") != "captured" or not observation.get("visible_text_length"):
            raise ValueError(f"{domain}: final evidence is not a non-empty captured page")
        rule = RULES[domain]
        haystack = evidence_text(observation)
        for index, alternatives in enumerate(rule["anchors"], 1):
            if not any(key(value) in haystack for value in alternatives):
                raise ValueError(f"{domain}: stable anchor group {index} is absent from final evidence")
        label, label_reason = LABEL_OVERRIDES.get(domain, (original["label"], original["reason"]))
        case = {
            **original,
            "observed_final_url": observation["final_url"],
            "label": label,
            "reason": label_reason,
            "evidence": {
                "observation_state": observation["observation_state"],
                "http_status": observation["http_status"],
                "final_url": observation["final_url"],
                "title": observation["title"],
                "visible_text_length": observation["visible_text_length"],
                "navigation_error": observation["navigation_error"],
            },
            "rule_status": "ready_from_final_independent_browser_evidence",
        }
        contract = acceptance(case, rule)
        evidence_path, evidence_hash, named_count = write_evidence(
            case, observation, rule, evidence_file, evidence_file_hash, evidence_source
        )
        finalized.append(case)
        machine_rules.append({
            "id": case["id"], "domain": domain, "url": case["url"],
            "observed_final_url": observation["final_url"], "label": label,
            "rule_model": contract["schema"], "anchor_groups": rule["anchors"],
            "acceptance_contract": contract, "observed_matching_named_links": named_count,
            "min_content_chars": 1, "length_gate_applicable": False,
            "volatile_exclusions": ["current_items", "ordering", "rank", "price", "count", "date"],
            "evidence": evidence_path, "evidence_sha256": evidence_hash,
            "review_status": "ready_from_final_independent_browser_evidence",
        })
        task_prompt = prompt(case, contract)
        dataset_cases.append({
            "id": case["id"], "name": f"{domain} 首页", "url": case["url"],
            "category": "global_homepage", "expected_any": [], "description": case["reason"],
            "tags": ["global-popular", "ranked-domain-homepage", label],
            "required_facts": [], "forbidden_any": BLOCKED_TEXT,
            "min_content_chars": 1, "fact_coverage_threshold": 1.0,
            "ground_truth_evidence": [{"source": evidence_path,
                "supports": "独立 Chromium 首次加载证据中的稳定页面文字和列表结构"}],
            "task_prompt": task_prompt, "reference_answer": task_prompt,
            "source_benchmark": {
                "mode": "ranked_domain_homepage", "rank_metadata_used_for_scoring": False,
                "requested_ranked_domain": domain, "observed_final_url": observation["final_url"],
                "stable_annotation": {
                    "registry": "global-homepage-rules", "version": 1, "content_kind": label,
                    "stable_fields": ["task_related_page_text"],
                    "structural_only_fields": (["item_title", "detail_url"] if contract["schema"] == "named_link" else []),
                    "anchor_evidence": evidence_path, "anchor_evidence_sha256": evidence_hash,
                    "record_requirement": ("At least 3 complete unique named links; additional legitimate records are allowed."
                        if contract["schema"] == "named_link" else
                        "No structured records; every registered stable page-text group is required."),
                    "task_decision_scope": "structure_and_explicit_stable_content",
                    "live_factual_correctness": None,
                    "note": ("Independent browser evidence came from the ranked-domain homepage; only a proven locale-variant homepage uses a fixed-language URL. No provider output or recursive page was used."),
                },
            },
            "ground_truth_main_content": "", "content_score_applicable": False,
            "track": "challenge", "required_patterns": [],
            "source_verification": {
                "state": "valid", "checked_at": "2026-09-11",
                "method": "Independent headless Chromium initial-page capture with hash-bound stable evidence",
                "reference_sha256": hashlib.sha256(b"").hexdigest(),
                "note": "Stable rule evidence only; current members and dynamic values are not frozen truth.",
            },
            "task_kind": "structure", "acceptance_contract": contract, "body_requirements": {},
        })

    selected_payload = {
        **{key: value for key, value in selection.items() if key not in {"cases", "excluded"}},
        "purpose": "Final selected and labeled ranked-domain homepage cohort",
        "source": {**selection["source"], "final_rule_capture_file": str(CAPTURE.relative_to(ROOT)),
            "final_rule_capture_sha256": capture_hash,
            "visual_spot_check_file": str(VISUAL_SPOT_CHECK.relative_to(ROOT)),
            "visual_spot_check_sha256": visual_spot_check_hash,
            "locale_review_file": str(LOCALE_REVIEW.relative_to(ROOT)),
            "locale_review_sha256": locale_review_hash},
        "selected_count": len(finalized),
        "excluded_non_adult_count": len(selection["excluded"]) + len(deferred_exclusions),
        "cases": finalized,
        "excluded": selection["excluded"] + deferred_exclusions,
    }
    SELECTED_OUTPUT.write_text(json.dumps(selected_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    selected_hash = hashlib.sha256(SELECTED_OUTPUT.read_bytes()).hexdigest()
    rules_payload = {
        "schema_version": 1, "purpose": "Per-homepage executable acceptance rules",
        "status": "frozen", "source_selection": str(SELECTED_OUTPUT.relative_to(ROOT)),
        "source_selection_sha256": selected_hash,
        "source_capture": str(CAPTURE.relative_to(ROOT)), "source_capture_sha256": capture_hash,
        "visual_spot_check": str(VISUAL_SPOT_CHECK.relative_to(ROOT)),
        "visual_spot_check_sha256": visual_spot_check_hash,
        "variant_review": str(VARIANT_REVIEW.relative_to(ROOT)),
        "variant_review_sha256": variant_review_hash,
        "locale_review": str(LOCALE_REVIEW.relative_to(ROOT)),
        "locale_review_sha256": locale_review_hash,
        "common": {"minimum_content_chars": 1, "length_gate_applicable": False,
            "all_registered_anchor_groups_required": True, "recursive_navigation_allowed": False,
            "dynamic_members_and_values_frozen": False},
        "cases": machine_rules,
    }
    RULES_OUTPUT.write_text(json.dumps(rules_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rules_hash = hashlib.sha256(RULES_OUTPUT.read_bytes()).hexdigest()
    dataset = {
        "schema_version": 1, "id": "global-homepages", "name": "全球热门域名首页 Web Fetch 评测",
        "tool": "web_fetch",
        "dynamic_scoring": "首页按独立观察的稳定文字与页面实际结构验收；不冻结动态条目和值。",
        "coverage_plan": {"challenge": ["lexmount-global"]},
        "comparison": {"kind": "ranked_domain_homepage", "timeout_seconds": 45,
            "concurrency": 1, "runs_per_case": 1, "publication_state": "frozen_not_run",
            "require_independent_result_review": False,
            "source_rule_registry": str(RULES_OUTPUT.relative_to(ROOT)),
            "source_rule_registry_sha256": rules_hash,
            "preflight_visual_spot_check": str(VISUAL_SPOT_CHECK.relative_to(ROOT)),
            "preflight_visual_spot_check_sha256": visual_spot_check_hash,
            "rule_variant_review": str(VARIANT_REVIEW.relative_to(ROOT)),
            "rule_variant_review_sha256": variant_review_hash,
            "locale_review": str(LOCALE_REVIEW.relative_to(ROOT)),
            "locale_review_sha256": locale_review_hash},
        "cases": dataset_cases,
    }
    DATASET_OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _, loaded = load_dataset(DATASET_OUTPUT)
    if len(loaded) != len(finalized):
        raise SystemExit("Generated dataset case count changed during load")
    write_review(finalized, machine_rules, selected_payload["excluded"], visual_spot_check_payload["checks"])
    print(f"Built {len(finalized)} selected homepages, rules and executable dataset")
    print(f"Schemas: named_link={sum(r['rule_model']=='named_link' for r in machine_rules)}, "
          f"page_identity={sum(r['rule_model']=='page_identity' for r in machine_rules)}")


def write_review(cases: list[dict], rules: list[dict], exclusions: list[dict], visual_checks: list[dict]) -> None:
    observed_visual = [item for item in visual_checks if item.get("state") == "observed"]
    not_rechecked = [item for item in visual_checks if item.get("state") != "observed"]
    lines = [
        "# 全球热门域名首页规则审阅", "",
        "> 本文件由 `scripts/build_global_homepage_suite.py` 从最终独立 Chromium 证据机械生成。默认请求榜单域名根首页；只有独立观察证明根首页语言会变化时，才使用同站固定语言首页 URL。禁止人工替换成文章、视频或商品详情页。",
        "", "## 范围", "",
        f"- 最终纳入：{len(cases)} 个；非成人候选排除：{len(exclusions)} 个。目标数量不是配额。",
        "- 成人域名已在候选生成阶段按用户政策省略，未访问，也未写入本文件。",
        "- 登录页、空白页、错误页、验证码/安全验证页和当前地区不可用页均排除。",
        "- `named_link` 要求至少 3 个唯一具名详情链接；`page_identity` 要求命中全部登记的稳定身份/功能文字。",
        "- 不设置实质最低字符数；兼容字段为 `min_content_chars: 1`。不冻结当前条目、排名、价格、日期或数量。",
        "", "## 冻结前可视抽查", "",
        f"- 抽查并成功观察：{len(observed_visual)} 个；未通过当前可视浏览器复查：{len(not_rechecked)} 个。",
        "- 抽查记录：`research/global-homepage-visual-spot-check-2026-09-11.json`。它补充而不替代完整无头 Chromium 证据。",
        "- YouTube 在可视匿名首页呈现公开推荐视频流，因此标签修正为 `content_list`；视频标题等动态成员仍不作为固定得分点。",
        "- 对独立观察证明会出现地区/语言变体的首页，改用已验证的同站固定语言 URL；没有可靠固定方式的 Roblox 明确暂缓，不混合多语言规则。",
        "- 每项契约只登记所选首页独立观察到的一种语言；不允许用多语种候选词拼接成一次通过。",
        "", "## 逐站规则", "",
        "| 案例 | 请求 URL → 观察到的最终 URL | 标签 | 模型 | 稳定文字组 | 证据中匹配的具名链接 |",
        "|---|---|---|---|---|---:|",
    ]
    for case, rule in zip(cases, rules):
        anchors = "；".join(" / ".join(group) for group in rule["anchor_groups"])
        anchors_cell = anchors.replace("|", "\\|")
        lines.append(f"| `{case['id']}` | {case['url']} → {case['observed_final_url']} | `{case['label']}` | "
            f"`{rule['rule_model']}` | {anchors_cell} | {rule['observed_matching_named_links']} |")
    lines.extend(["", "## 排除记录", ""])
    for item in exclusions:
        lines.append(f"- `{item['domain']}` — `{item['reason_code']}` — {item['reason']}")
    lines.extend(["", "## 人工复核栏", "", "- 规则文件：`research/global-homepage-rules.json`",
        "- 测试集：`evaluation/global-homepages-dataset.json`",
        "- 本次规则构建不发起供应商调用；已有历史调用不参与参考规则制定。", ""])
    REVIEW_OUTPUT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
