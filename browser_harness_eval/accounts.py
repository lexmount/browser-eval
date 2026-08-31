from __future__ import annotations

import os
from typing import Any


ACCOUNT_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "lexmount-global",
        "provider": "Lexmount Global",
        "priority": "A · core benchmark",
        "registration_url": "https://browser.lexmount.com/settings/api-keys",
        "key_url": "https://browser.lexmount.com/settings/api-keys",
        "env_vars": ["LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID"],
        "required": True,
        "implemented": True,
        "free_note": "Internal-testing access; use a key and project ID from the same global project.",
        "steps": "Sign in, open Settings → API Keys, create/copy an API key and copy its Project ID. Use api.lexmount.com.",
    },
    {
        "id": "lexmount-china",
        "provider": "Lexmount China",
        "priority": "B · regional comparison",
        "registration_url": "https://browser.lexmount.cn/settings/api-keys",
        "key_url": "https://browser.lexmount.cn/settings/api-keys",
        "env_vars": ["LEXMOUNT_CN_API_KEY", "LEXMOUNT_CN_PROJECT_ID"],
        "required": True,
        "implemented": True,
        "free_note": "Requires a China-region project/key pair.",
        "steps": "Sign in to the China console, create/copy the key and matching Project ID. Use api.lexmount.cn.",
    },
    {
        "id": "firecrawl",
        "provider": "Firecrawl",
        "priority": "A · core benchmark",
        "registration_url": "https://www.firecrawl.dev/signin",
        "key_url": "https://www.firecrawl.dev/app/api-keys",
        "env_vars": ["FIRECRAWL_API_KEY"],
        "required": True,
        "implemented": True,
        "free_note": "Keyless tier exists but is rate-limited; use a free account for reproducible runs.",
        "steps": "Create an account, open the Firecrawl dashboard API Keys page, create a key beginning with fc-.",
    },
    {
        "id": "jina-reader",
        "provider": "Jina Reader",
        "priority": "A · core benchmark",
        "registration_url": "https://jina.ai/reader/",
        "key_url": "https://jina.ai/api-dashboard/",
        "env_vars": ["JINA_API_KEY"],
        "required": False,
        "implemented": True,
        "free_note": "No key: 20 RPM. Free key: higher rate limit. A key does not bypass blocked sites.",
        "steps": "Reader works without registration. For a key, use Get your API key / API Key & Billing on the Reader page.",
    },
    {
        "id": "tavily",
        "provider": "Tavily Extract",
        "priority": "B · Agent-native comparison",
        "registration_url": "https://app.tavily.com",
        "key_url": "https://app.tavily.com/home",
        "env_vars": ["TAVILY_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "Official quickstart states 1,000 free API credits/month without a card.",
        "steps": "Create/sign in to Tavily Platform and copy an API key from the dashboard.",
    },
    {
        "id": "exa",
        "provider": "Exa Contents",
        "priority": "B · Agent-native comparison",
        "registration_url": "https://dashboard.exa.ai/",
        "key_url": "https://dashboard.exa.ai/api-keys",
        "env_vars": ["EXA_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "Dashboard account required; usage is credit/budget controlled.",
        "steps": "Create/sign in to the Exa dashboard, open API Keys, create a key and set a budget if required.",
    },
    {
        "id": "browserbase",
        "provider": "Browserbase Fetch",
        "priority": "B · Agent-native comparison",
        "registration_url": "https://www.browserbase.com/sign-up",
        "key_url": "https://www.browserbase.com/overview",
        "env_vars": ["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"],
        "required": True,
        "implemented": False,
        "free_note": "Free account available; dashboard shows API key and Project ID.",
        "steps": "Sign up, create/select a project, copy API key and Project ID from the Overview dashboard.",
    },
    {
        "id": "brightdata",
        "provider": "Bright Data Web Unlocker",
        "priority": "C · unlock dataset",
        "registration_url": "https://brightdata.com/cp/start",
        "key_url": "https://brightdata.com/cp/zones",
        "env_vars": ["BRIGHTDATA_API_TOKEN", "BRIGHTDATA_UNLOCKER_ZONE"],
        "required": True,
        "implemented": False,
        "free_note": "Account, billing/trial eligibility and a Web Unlocker zone are required.",
        "steps": "Create an account, create a Web Unlocker zone, then copy the API token and zone name.",
    },
    {
        "id": "scrapingbee",
        "provider": "ScrapingBee",
        "priority": "C · scraping API comparison",
        "registration_url": "https://app.scrapingbee.com/account/register",
        "key_url": "https://app.scrapingbee.com/account/manage/api_key",
        "env_vars": ["SCRAPINGBEE_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "Trial/account limits apply; production plans have a monthly minimum.",
        "steps": "Register, open Account → API Key, and copy the key.",
    },
    {
        "id": "zyte",
        "provider": "Zyte API",
        "priority": "C · scraping API comparison",
        "registration_url": "https://app.zyte.com/",
        "key_url": "https://app.zyte.com/",
        "env_vars": ["ZYTE_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "Official documentation states $5 initial credit for the first month.",
        "steps": "Sign up, open the Zyte API section in the dashboard and create/copy an API key.",
    },
    {
        "id": "zenrows",
        "provider": "ZenRows",
        "priority": "C · scraping API comparison",
        "registration_url": "https://app.zenrows.com/register",
        "key_url": "https://app.zenrows.com/builder",
        "env_vars": ["ZENROWS_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "Account/trial limits apply.",
        "steps": "Register and copy the API key shown in the dashboard/request builder.",
    },
    {
        "id": "scrapfly",
        "provider": "Scrapfly",
        "priority": "C · scraping API comparison",
        "registration_url": "https://scrapfly.io/register",
        "key_url": "https://scrapfly.io/dashboard",
        "env_vars": ["SCRAPFLY_API_KEY"],
        "required": True,
        "implemented": False,
        "free_note": "Account/trial limits apply.",
        "steps": "Register, open the dashboard and copy the project API key.",
    },
    {
        "id": "oxylabs",
        "provider": "Oxylabs Web Scraper API",
        "priority": "C · scraping API comparison",
        "registration_url": "https://dashboard.oxylabs.io/en/registration",
        "key_url": "https://dashboard.oxylabs.io/",
        "env_vars": ["OXYLABS_USERNAME", "OXYLABS_PASSWORD"],
        "required": True,
        "implemented": False,
        "free_note": "Trial or sales approval may be required for the selected product.",
        "steps": "Register or request a trial, enable Web Scraper API and copy its user credentials.",
    },
]


def credential_state(item: dict[str, Any]) -> str:
    present = [bool(os.getenv(name, "").strip()) for name in item["env_vars"]]
    if all(present):
        return "READY"
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
        support = "adapter ready" if item["implemented"] else "adapter planned"
        print(f"[{state}] {item['provider']} — {item['priority']} — {support}")
        print(f"  Register: {item['registration_url']}")
        print(f"  Get key:  {item['key_url']}")
        print(f"  Set:      {', '.join(item['env_vars'])}")
        print(f"  Steps:    {item['steps']}")
        print(f"  Access:   {item['free_note']}")
        print()
    print("COPY INTO .env AND FILL VALUES")
    print("-" * 72)
    print(env_template(), end="")
