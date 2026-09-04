from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import EvalCase, ProviderResult


class Provider(ABC):
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = config
        self.type = str(config.get("type", name))

    @property
    def min_interval_seconds(self) -> float:
        """Minimum cooldown from one response completion to the next request start."""
        return max(0.0, float(self.config.get("min_interval_seconds", 0)))

    @abstractmethod
    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        raise NotImplementedError

    def validate(self) -> list[str]:
        return []


def build_provider(name: str, config: dict[str, Any]) -> Provider:
    provider_type = config.get("type")
    if provider_type in {"headless_html", "headless_markdown"}:
        from .headless import HeadlessProvider
        return HeadlessProvider(name, config)
    if provider_type == "moli_webfetch":
        from .moli import MoliWebFetchProvider

        return MoliWebFetchProvider(name, config)
    if provider_type == "http":
        from .http import HttpProvider

        return HttpProvider(name, config)
    if provider_type == "cowagent_builtin":
        from .cowagent import CowAgentBuiltinProvider

        return CowAgentBuiltinProvider(name, config)
    if provider_type in {"lexmount", "lexmount_cn"}:
        from .lexmount import LexmountProvider

        return LexmountProvider(name, config)
    if provider_type == "firecrawl":
        from .firecrawl import FirecrawlProvider

        return FirecrawlProvider(name, config)
    if provider_type == "tavily_extract":
        from .tavily import TavilyExtractProvider

        return TavilyExtractProvider(name, config)
    if provider_type == "exa_contents":
        from .exa import ExaContentsProvider

        return ExaContentsProvider(name, config)
    if provider_type == "cloudflare_markdown":
        from .cloudflare import CloudflareMarkdownProvider

        return CloudflareMarkdownProvider(name, config)
    if provider_type == "cloudflare_content":
        from .cloudflare import CloudflareContentProvider

        return CloudflareContentProvider(name, config)
    if provider_type == "subprocess_jsonl":
        from .subprocess_jsonl import SubprocessJsonlProvider

        return SubprocessJsonlProvider(name, config)
    if provider_type == "fixture":
        from .fixture import FixtureProvider

        return FixtureProvider(name, config)
    raise ValueError(f"unknown provider type: {provider_type!r}")
