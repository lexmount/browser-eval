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
        "provider": "Cloudflare Browser Run /markdown、/scrape 与 Workers AI /ai/tomarkdown",
        "priority": "B · 已知 URL 抽取",
        "registration_url": "https://dash.cloudflare.com/sign-up",
        "key_url": "https://dash.cloudflare.com/profile/api-tokens",
        "env_vars": ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"],
        "required": True,
        "implemented": True,
        "free_note": "Browser Run 可用 Workers Free 额度。Browser Run 需要 Browser Rendering Edit；文件转换 API 接受 Workers AI Read 或 Edit。两类产品的配额和计费分开。",
        "steps": "注册 Cloudflare，复制 Account ID；创建覆盖 Account → Browser Rendering → Edit 与 Account → Workers AI → Read 的自定义 token。官方 Workers AI REST 入门建议 Read 和 Edit 都授予；运行前用 GET /client/v4/user/tokens/verify 验证。",
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

REPORT_PROVIDER_ACCOUNT_IDS = {
    "cloudflare-markdown": "cloudflare-browser-rendering",
    "exa": "exa",
    "firecrawl": "firecrawl",
    "lexmount-cn": "lexmount-cn",
    "lexmount-global": "lexmount-global",
    "tavily": "tavily",
}

REPORT_LOCAL_ACCESS: dict[str, dict[str, Any]] = {
    "fixture": {
        "provider": "Fixture 测试适配器",
        "registration_url": "https://github.com/lexmount/browser-eval",
        "key_url": "https://github.com/lexmount/browser-eval",
        "env_vars": [],
        "steps": "仓库内测试适配器；只用于确定性回归测试。",
        "free_note": "不需要账号或环境变量。",
    },
    "moli": {
        "provider": "Moli 1.1.5",
        "registration_url": "https://github.com/lexmount/moli/releases/tag/v1.1.5",
        "key_url": "https://github.com/lexmount/moli/releases/tag/v1.1.5",
        "env_vars": ["MOLI_BINARY"],
        "steps": "安装并固定官方 v1.1.5 二进制；不需要供应商账号或 API Key。",
        "free_note": "本地 CLI。",
    },
    "moli-1.1.7": {
        "provider": "Moli 1.1.7",
        "registration_url": "https://github.com/lexmount/moli/releases/tag/v1.1.7",
        "key_url": "https://github.com/lexmount/moli/releases/tag/v1.1.7",
        "env_vars": ["MOLI_1_1_7_BINARY"],
        "steps": "安装并固定官方 v1.1.7 二进制；不需要供应商账号或 API Key。",
        "free_note": "本地 CLI。",
    },
    "moli-webfetch": {
        "provider": "Moli WebFetch",
        "registration_url": "https://github.com/lexmount/moli/tree/main/skills/moli-webfetch",
        "key_url": "https://github.com/lexmount/moli/releases",
        "env_vars": ["MOLI_BINARY"],
        "steps": "安装并固定本轮 Moli 可执行文件；不需要供应商账号或 API Key。",
        "free_note": "本地 CLI；二进制路径和版本属于运行证据。",
    },
    "cowagent-built-in": {
        "provider": "CowAgent 内置 Web Fetch",
        "registration_url": "https://github.com/zhayujie/CowAgent",
        "key_url": "https://github.com/zhayujie/CowAgent",
        "env_vars": ["COWAGENT_REPO", "COWAGENT_BENCHMARK_WORKSPACE"],
        "steps": "检出并固定 CowAgent 源码，配置仓库路径和隔离工作目录；不需要供应商 API Key。",
        "free_note": "本地项目内置实现。",
    },
    "openclaw-built-in": {
        "provider": "OpenClaw 内置 Web Fetch",
        "registration_url": "https://github.com/openclaw/openclaw",
        "key_url": "https://github.com/openclaw/openclaw/blob/main/docs/tools/web-fetch.md",
        "env_vars": ["OPENCLAW_REPO", "OPENCLAW_NODE", "OPENCLAW_PNPM"],
        "steps": "检出并固定 OpenClaw 源码、Node 与 pnpm；本轮关闭 Firecrawl 回退。",
        "free_note": "本地项目内置实现；不需要云端抓取 Key。",
    },
    "deepseek-harness-built-in": {
        "provider": "DeepSeek Harness 内置 Web Fetch",
        "registration_url": "https://github.com/deepseek-ai/deepseek-harness",
        "key_url": "https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/web",
        "env_vars": ["DEEPSEEK_HARNESS_REPO", "OPENCLAW_NODE", "DEEPSEEK_PNPM"],
        "steps": "检出并固定 DeepSeek Harness 源码、Node、pnpm 与构建依赖；本轮不调用聊天模型。",
        "free_note": "本地项目内置实现；不需要模型 API Key。",
    },
    "langchain-webbase": {
        "provider": "LangChain WebBaseLoader",
        "registration_url": "https://reference.langchain.com/python/langchain-community/document_loaders/web_base/WebBaseLoader",
        "key_url": "https://python.langchain.com/docs/how_to/document_loader_web/",
        "env_vars": ["OSS_PYTHON"],
        "steps": "使用仓库冻结的 Python 环境运行；OSS_PYTHON 只在显式覆盖 worker 解释器时设置。",
        "free_note": "本地开源库；不需要供应商账号。",
    },
    "crawl4ai": {
        "provider": "Crawl4AI",
        "registration_url": "https://github.com/unclecode/crawl4ai",
        "key_url": "https://docs.crawl4ai.com/core/installation/",
        "env_vars": ["OSS_PYTHON"],
        "steps": "使用仓库冻结的 Python 环境及匹配的浏览器运行；OSS_PYTHON 只在显式覆盖 worker 解释器时设置。",
        "free_note": "本地开源库；不需要供应商账号。",
    },
}


def report_access_registry(
    providers: set[str], *, fixture_providers: set[str] | None = None
) -> list[dict[str, Any]]:
    """Return access requirements only for methods measured in one report."""
    account_by_id = {item["id"]: item for item in ACCOUNT_REGISTRY}
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    fixture_providers = fixture_providers or set()
    for provider in sorted(providers):
        account_id = REPORT_PROVIDER_ACCOUNT_IDS.get(provider)
        if account_id:
            if account_id not in account_by_id:
                missing.append(provider)
                continue
            rows.append(account_by_id[account_id])
        elif provider in REPORT_LOCAL_ACCESS:
            rows.append(REPORT_LOCAL_ACCESS[provider])
        elif provider in fixture_providers:
            rows.append({**REPORT_LOCAL_ACCESS["fixture"], "provider": provider})
        else:
            missing.append(provider)
    if missing:
        raise ValueError(f"missing report access metadata for providers: {', '.join(missing)}")
    return rows


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
