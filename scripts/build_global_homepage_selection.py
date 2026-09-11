#!/usr/bin/env python3
"""Freeze homepage eligibility decisions from one browser-observation batch."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "research/similarweb-global-candidate-domains-2026-05.json"
OBSERVATIONS = ROOT / "research/global-site-root-observations-2026-09-11.json"
OUTPUT = ROOT / "research/global-homepage-selection.json"


# Labels describe the ranked domain's own root page after natural redirects.
# They do not select a different content URL.
SELECTED = {
    "google.com": "entry_page",
    "youtube.com": "content_list",
    "chatgpt.com": "entry_page",
    "reddit.com": "content_list",
    "whatsapp.com": "landing_page",
    "bing.com": "entry_page",
    "wikipedia.org": "entry_page",
    "gemini.google.com": "entry_page",
    "yahoo.co.jp": "content_list",
    "yahoo.com": "content_list",
    "amazon.com": "product_catalog",
    "baidu.com": "entry_page",
    "naver.com": "content_list",
    "netflix.com": "landing_page",
    "cloud.microsoft": "landing_page",
    "bilibili.com": "content_list",
    "temu.com": "product_catalog",
    "dzen.ru": "content_list",
    "microsoft.com": "landing_page",
    "twitch.tv": "content_list",
    "weather.com": "entry_page",
    "canva.com": "landing_page",
    "news.yahoo.co.jp": "content_list",
    "fandom.com": "content_list",
    "globo.com": "content_list",
    "mail.ru": "content_list",
    "samsung.com": "landing_page",
    "duckduckgo.com": "entry_page",
    "discord.com": "landing_page",
    "github.com": "landing_page",
    "spotify.com": "entry_page",
    "nytimes.com": "content_list",
    "zoom.us": "landing_page",
    "booking.com": "entry_page",
    "brave.com": "landing_page",
    "roblox.com": "landing_page",
    "apple.com": "landing_page",
    "walmart.com": "product_catalog",
    "espn.com": "content_list",
    "bbc.com": "content_list",
    "paypal.com": "landing_page",
    "ya.ru": "entry_page",
    "deepseek.com": "landing_page",
    "indeed.com": "entry_page",
    "rakuten.co.jp": "product_catalog",
    "telegram.org": "landing_page",
    "cnn.com": "content_list",
    "namu.wiki": "landing_page",
    "shein.com": "product_catalog",
    "qq.com": "content_list",
    "douyin.com": "content_list",
    "usps.com": "landing_page",
    "instructure.com": "landing_page",
    "primevideo.com": "landing_page",
    "rutube.ru": "content_list",
}


EXCLUDED = {
    "facebook.com": ("login_page", "首页主体是账号登录/注册表单。"),
    "instagram.com": ("login_page", "首页主体是账号登录/注册表单。"),
    "x.com": ("access_denied", "首次加载返回 HTTP 403 且没有可见正文。"),
    "tiktok.com": ("capture_error", "浏览器未能取得可审阅页面。"),
    "yandex.ru": ("duplicate_final_page", "自然重定向到 dzen.ru，与保留案例完全重合。"),
    "linkedin.com": ("login_page", "页面标题与主体均以登录或注册为中心。"),
    "bet.br": ("blank_or_error", "最终进入浏览器错误页且可见正文为空。"),
    "pinterest.com": ("login_page", "首次页面包含密码登录/注册界面；不把登录门后的内容作为首页正文。"),
    "live.com": ("capture_error", "浏览器未能取得可审阅页面。"),
    "vk.com": ("login_page", "首次页面主体是 VK 登录和注册入口。"),
    "claude.ai": ("login_page", "自然重定向到 /login，主体是登录表单。"),
    "t.me": ("duplicate_final_page", "自然重定向到 telegram.org，与保留案例完全重合。"),
    "ebay.com": ("generic_error", "HTTP 403 页面显示站点错误信息。"),
    "imdb.com": ("captcha_or_security_verification", "页面为 Human Verification 安全检查。"),
    "cricbuzz.com": ("access_denied", "HTTP 403 Access Denied。"),
    "ozon.ru": ("generic_error", "HTTP 403，页面提示连接不可用。"),
    "aliexpress.com": ("login_page", "自然重定向到登录页。"),
    "bbc.co.uk": ("duplicate_final_page", "自然重定向到 bbc.com，与保留案例完全重合。"),
    "office.com": ("duplicate_final_page", "自然重定向到 m365.cloud.microsoft，与 cloud.microsoft 保留案例重合。"),
    "docomo.ne.jp": ("not_found", "HTTP 404，正文仅为 Not Found。"),
    "amazon.in": ("regional_duplicate", "与 amazon.com 属于高度重复的 Amazon 地区首页；主集透明去重。"),
    "amazon.co.jp": ("regional_duplicate", "与 amazon.com 属于高度重复的 Amazon 地区首页；主集透明去重。"),
    "msn.com": ("blank", "HTTP 200 但渲染后可见正文长度为 0。"),
    "etsy.com": ("blank_or_blocked", "HTTP 403 且渲染后可见正文为空。"),
    "music.youtube.com": ("unavailable", "页面明确显示该服务在当前地区不可用。"),
    "disneyplus.com": ("blank_or_error", "最终进入浏览器错误页且可见正文为空。"),
    "amazon.de": ("regional_duplicate", "与 amazon.com 属于高度重复的 Amazon 地区首页；主集透明去重。"),
    "adobe.com": ("blank_or_error", "最终进入浏览器错误页且可见正文为空。"),
    "chat.deepseek.com": ("login_page", "自然重定向到 /sign_in，主体是登录表单。"),
    "hbomax.com": ("access_denied", "HTTP 403 Access Denied。"),
    "zillow.com": ("captcha_or_security_verification", "HTTP 403，要求按住控件完成人机验证。"),
    "amazon.co.uk": ("regional_duplicate", "与 amazon.com 属于高度重复的 Amazon 地区首页；主集透明去重。"),
    "ok.ru": ("login_page", "首次页面主体是社交网络登录和注册入口。"),
    "wildberries.ru": ("captcha_or_security_verification", "HTTP 498，页面仅显示正在检查浏览器。"),
    "quora.com": ("login_page", "首页主体是第三方登录、邮箱注册和密码登录表单。"),
}


LABEL_REASONS = {
    "entry_page": "首页主要提供搜索、问答、位置查询或应用入口；按入口页模型核验。",
    "content_list": "首页直接呈现多条公开内容或导航栏目；按实际列表与稳定页面文字核验。",
    "product_catalog": "首页直接呈现商品或购物目录内容；不冻结当前商品、价格或排序。",
    "landing_page": "首页包含公开品牌、产品或服务说明，不是纯登录页。",
}


def compact_observation(item: dict) -> dict:
    return {
        key: item.get(key)
        for key in (
            "observation_state", "http_status", "final_url", "title",
            "visible_text_length", "has_password_input", "has_login_language",
            "has_captcha_language", "has_error_language", "navigation_error",
        )
    }


def main() -> None:
    candidates_raw = CANDIDATES.read_bytes()
    observations_raw = OBSERVATIONS.read_bytes()
    candidates = json.loads(candidates_raw)["domains"]
    observed = {item["domain"]: item for item in json.loads(observations_raw)["observations"]}
    if len(candidates) != 90 or len(observed) != 90:
        raise SystemExit("Expected exactly 90 policy-filtered candidates and observations")
    if set(SELECTED) & set(EXCLUDED):
        raise SystemExit("A domain cannot be both selected and excluded")
    if set(candidates) != set(SELECTED) | set(EXCLUDED):
        missing = set(candidates) - set(SELECTED) - set(EXCLUDED)
        extra = set(SELECTED) | set(EXCLUDED) - set(candidates)
        raise SystemExit(f"Decisions do not partition candidates; missing={missing}, extra={extra}")

    cases = []
    exclusions = []
    for index, domain in enumerate(candidates, 1):
        observation = observed[domain]
        if domain in SELECTED:
            label = SELECTED[domain]
            cases.append({
                "id": f"global-homepage-{index:03d}-{domain.replace('.', '-')}",
                "domain": domain,
                "url": f"https://{domain}/",
                "observed_final_url": observation.get("final_url"),
                "label": label,
                "reason": LABEL_REASONS[label],
                "evidence": compact_observation(observation),
                "rule_status": "browser_evidence_capture_required",
            })
        else:
            reason_code, reason = EXCLUDED[domain]
            exclusions.append({
                "domain": domain,
                "url": f"https://{domain}/",
                "reason_code": reason_code,
                "reason": reason,
                "evidence": compact_observation(observation),
            })

    result = {
        "schema_version": 1,
        "purpose": "Ranked-domain homepage selection; representative content substitutions are excluded",
        "source": {
            "candidate_file": str(CANDIDATES.relative_to(ROOT)),
            "candidate_file_sha256": hashlib.sha256(candidates_raw).hexdigest(),
            "observation_file": str(OBSERVATIONS.relative_to(ROOT)),
            "observation_file_sha256": hashlib.sha256(observations_raw).hexdigest(),
            "observation_method": "Playwright Chromium, fresh anonymous context per ranked domain, initial load only",
            "adult_policy": "Adult-content domains were omitted upstream and never visited.",
        },
        "selection_policy": {
            "ranked_domain_root_input": True,
            "natural_redirects_allowed": True,
            "manual_content_page_substitution_allowed": False,
            "excluded_page_types": [
                "login_page", "blank", "captcha_or_security_verification",
                "access_denied", "generic_error", "unavailable",
            ],
            "exact_final_page_duplicates_deduplicated": True,
            "highly_repetitive_amazon_regional_homepages_deduplicated": True,
            "target_count_is_not_a_quota": True,
        },
        "selected_count": len(cases),
        "excluded_non_adult_count": len(exclusions),
        "cases": cases,
        "excluded": exclusions,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Selected {len(cases)} homepages; excluded {len(exclusions)} non-adult candidates")
    print(f"Built {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
