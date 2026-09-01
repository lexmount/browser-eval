from __future__ import annotations

import os
from typing import Any


ACCOUNT_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "lexmount-global",
        "provider": "Lexmount Global",
        "priority": "A · 核心评测",
        "registration_url": "https://browser.lexmount.com/settings/api-keys",
        "key_url": "https://browser.lexmount.com/settings/api-keys",
        "env_vars": ["LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID"],
        "required": True,
        "implemented": True,
        "free_note": "内测准入；API key 与 Project ID 必须来自同一个 Global 项目。",
        "steps": "登录后打开 Settings → API Keys，创建或复制 API key，并复制对应 Project ID；本评测使用 api.lexmount.com。",
    },
    {
        "id": "firecrawl",
        "provider": "Firecrawl",
        "priority": "A · 核心评测",
        "registration_url": "https://www.firecrawl.dev/signin",
        "key_url": "https://www.firecrawl.dev/app/api-keys",
        "env_vars": ["FIRECRAWL_API_KEY"],
        "required": True,
        "implemented": True,
        "free_note": "存在无 key 的限流层；可复现评测应使用免费账号。",
        "steps": "注册账号，打开 Firecrawl 控制台 API Keys 页面，创建以 fc- 开头的 key。",
    },
    {
        "id": "lexmount-cn",
        "provider": "Lexmount 中国区",
        "priority": "A · 核心评测",
        "registration_url": "https://browser.lexmount.cn/",
        "key_url": "https://browser.lexmount.cn/",
        "env_vars": ["LEXMOUNT_CN_API_KEY", "LEXMOUNT_CN_PROJECT_ID"],
        "required": True,
        "implemented": True,
        "free_note": "API key 与 Project ID 必须来自同一个中国区项目。",
        "steps": "登录中国区控制台，复制同一项目下的 API key 与 Project ID；本评测使用 api.lexmount.cn。",
    },
    {
        "id": "tavily",
        "provider": "Tavily Extract",
        "priority": "B · 已知 URL 抽取",
        "registration_url": "https://app.tavily.com",
        "key_url": "https://app.tavily.com/home",
        "env_vars": ["TAVILY_API_KEY"],
        "required": True,
        "implemented": True,
        "free_note": "官方快速开始说明每月有 1,000 免费 API credits，无需绑卡。",
        "steps": "注册或登录 Tavily Platform，从控制台复制 API key。",
    },
    {
        "id": "exa",
        "provider": "Exa Contents",
        "priority": "B · 已知 URL 抽取",
        "registration_url": "https://dashboard.exa.ai/",
        "key_url": "https://dashboard.exa.ai/api-keys",
        "env_vars": ["EXA_API_KEY"],
        "required": True,
        "implemented": True,
        "free_note": "需要控制台账号；用量由 credits/预算控制。",
        "steps": "注册或登录 Exa 控制台，打开 API Keys，创建 key；如系统要求则设置预算。",
    },
    {
        "id": "browserbase",
        "provider": "Browserbase Fetch",
        "priority": "B · 已知 URL 抓取候选",
        "registration_url": "https://www.browserbase.com/sign-up",
        "key_url": "https://www.browserbase.com/overview",
        "env_vars": ["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"],
        "required": True,
        "implemented": False,
        "free_note": "官方页面存在契约冲突；打分前必须先以账号内实际接口核验。",
        "steps": "注册后创建或选择项目，从 Overview 控制台复制 API key 与 Project ID。",
    },
    {
        "id": "cloudflare-browser-rendering",
        "provider": "Cloudflare Browser Rendering /markdown",
        "priority": "B · 已知 URL 抽取",
        "registration_url": "https://dash.cloudflare.com/sign-up",
        "key_url": "https://dash.cloudflare.com/profile/api-tokens",
        "env_vars": ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"],
        "required": True,
        "implemented": True,
        "free_note": "Workers Free 可用 Browser Rendering；token 需要 Browser Rendering Edit 权限，并受账户限额约束。",
        "steps": "注册 Cloudflare，复制 32 位 Account ID，再创建自定义 API token：Account → Browser Rendering → Edit；运行前用 GET /client/v4/user/tokens/verify 验证。",
    },
    {
        "id": "bocha-web-search",
        "provider": "Bocha Web Search",
        "priority": "D · 查询到搜索结果",
        "registration_url": "https://open.bochaai.com/",
        "key_url": "https://open.bochaai.com/",
        "env_vars": ["BOCHA_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "搜索/摘要产品，不是任意已知 URL 抽取；应使用独立查询数据集。",
        "steps": "在博查开放平台注册，在控制台创建 API key，并填入 BOCHA_API_KEY。",
    },
    {
        "id": "baidu-ai-search",
        "provider": "Baidu Qianfan AI Search",
        "priority": "D · 查询到搜索结果",
        "registration_url": "https://console.bce.baidu.com/qianfan/ais/console/apiKey",
        "key_url": "https://console.bce.baidu.com/qianfan/ais/console/apiKey",
        "env_vars": ["QIANFAN_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "Web Search 按查询返回摘要、URL 与片段，不是任意 URL 抓取 API。",
        "steps": "注册并完成百度智能云认证，打开千帆 API Key 管理并创建或复制 key。",
    },
    {
        "id": "tencent-wsa",
        "provider": "Tencent Cloud Web Search API",
        "priority": "D · 查询到搜索结果",
        "registration_url": "https://console.cloud.tencent.com/wsa",
        "key_url": "https://console.cloud.tencent.com/wsa",
        "env_vars": ["TENCENT_WSA_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "返回标题、片段与来源 URL 的搜索 API，应与已知 URL 抽取分开评测。",
        "steps": "注册并完成认证，开通 Web Search API，再在控制台创建服务 API key。",
    },
    {
        "id": "aliyun-model-studio",
        "provider": "Alibaba Cloud Model Studio web_search + web_extractor",
        "priority": "E · 模型绑定检索",
        "registration_url": "https://bailian.console.aliyun.com/",
        "key_url": "https://bailian.console.aliyun.com/?tab=model#/api-key",
        "env_vars": ["DASHSCOPE_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "web_extractor 当前随 web_search 出现在模型响应中；模型 token 与工具调用都计入成本。",
        "steps": "注册并完成阿里云认证，开通百炼 Model Studio，在 API Key 管理创建 key。",
    },
    {
        "id": "gemini-url-context",
        "provider": "Google Gemini URL Context",
        "priority": "E · 模型绑定检索",
        "registration_url": "https://aistudio.google.com/",
        "key_url": "https://aistudio.google.com/apikey",
        "env_vars": ["GEMINI_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "模型把检索到的 URL 内容作为输入 token，返回答案/引用，而不是独立原始 Markdown。",
        "steps": "登录 Google AI Studio，创建 API key 并填入 GEMINI_API_KEY。",
    },
    {
        "id": "brightdata",
        "provider": "Bright Data Web Unlocker",
        "priority": "C · 解锁场景数据集",
        "registration_url": "https://brightdata.com/cp/start",
        "key_url": "https://brightdata.com/cp/zones",
        "env_vars": ["BRIGHTDATA_API_TOKEN", "BRIGHTDATA_UNLOCKER_ZONE"],
        "required": True,
        "implemented": False,
        "free_note": "需要账号、计费或试用资格，以及 Web Unlocker zone。",
        "steps": "注册账号，创建 Web Unlocker zone，再复制 API token 与 zone 名称。",
    },
    {
        "id": "scrapingbee",
        "provider": "ScrapingBee",
        "priority": "C · 抓取 API 对比",
        "registration_url": "https://app.scrapingbee.com/account/register",
        "key_url": "https://app.scrapingbee.com/account/manage/api_key",
        "env_vars": ["SCRAPINGBEE_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "受试用/账户限额约束；生产套餐有月度最低消费。",
        "steps": "注册后打开 Account → API Key，复制 key。",
    },
    {
        "id": "zyte",
        "provider": "Zyte API",
        "priority": "C · 抓取 API 对比",
        "registration_url": "https://app.zyte.com/",
        "key_url": "https://app.zyte.com/",
        "env_vars": ["ZYTE_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "官方文档说明首月有 5 美元初始 credit。",
        "steps": "注册后在控制台打开 Zyte API，创建或复制 API key。",
    },
    {
        "id": "zenrows",
        "provider": "ZenRows",
        "priority": "C · 抓取 API 对比",
        "registration_url": "https://app.zenrows.com/register",
        "key_url": "https://app.zenrows.com/builder",
        "env_vars": ["ZENROWS_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "受账号/试用限额约束。",
        "steps": "注册并复制控制台或 request builder 中显示的 API key。",
    },
    {
        "id": "scrapfly",
        "provider": "Scrapfly",
        "priority": "C · 抓取 API 对比",
        "registration_url": "https://scrapfly.io/register",
        "key_url": "https://scrapfly.io/dashboard",
        "env_vars": ["SCRAPFLY_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "受账号/试用限额约束。",
        "steps": "注册后打开控制台，复制项目 API key。",
    },
    {
        "id": "oxylabs",
        "provider": "Oxylabs Web Scraper API",
        "priority": "C · 抓取 API 对比",
        "registration_url": "https://dashboard.oxylabs.io/en/registration",
        "key_url": "https://dashboard.oxylabs.io/",
        "env_vars": ["OXYLABS_USERNAME", "OXYLABS_PASSWORD"],
        "required": True,
        "implemented": False,
        "free_note": "所选产品可能需要试用或销售审批。",
        "steps": "注册或申请试用，开通 Web Scraper API 并复制用户凭据。",
    },
]


def credential_state(item: dict[str, Any]) -> str:
    present = [bool(os.getenv(name, "").strip()) for name in item["env_vars"]]
    if all(present):
        return "PRESENT"
    if not item["required"]:
        return "OPTIONAL"
    return "MISSING"


def env_template() -> str:
    names: list[str] = []
    for item in ACCOUNT_REGISTRY:
        for name in item["env_vars"]:
            if name not in names:
                names.append(name)
    return "\n".join(f"{name}=" for name in names) + "\n"


def print_account_checklist() -> None:
    print("WEB FETCH ACCOUNT CHECKLIST")
    print("=" * 72)
    for item in ACCOUNT_REGISTRY:
        state = credential_state(item)
        support = "适配器已实现" if item["implemented"] else "适配器待实现"
        print(f"[{state}] {item['provider']} — {item['priority']} — {support}")
        print(f"  注册入口：{item['registration_url']}")
        print(f"  凭据入口：{item['key_url']}")
        print(f"  环境变量：{', '.join(item['env_vars'])}")
        print(f"  获取步骤：{item['steps']}")
        print(f"  准入说明：{item['free_note']}")
        print()
    print("COPY INTO .env AND FILL VALUES")
    print("-" * 72)
    print(env_template(), end="")
