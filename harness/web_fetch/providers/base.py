from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import EvalCase, ProviderResult


class Provider(ABC):
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = config
        self.type = str(config.get("type", name))

    def check_native_call(self, case: EvalCase) -> None:
        """Permit native task outputs, never case-specific retrieval assistance."""
        if not self.config.get("uniform_native_parameters"):
            return
        if case.retrieval_hints:
            raise ValueError("fixed native calls prohibit per-case retrieval hints")
        if case.task_kind not in {"body", "structure", "resource", "file"}:
            raise ValueError("fixed native calls require a declared native output kind")

    def capture_native_request(self, case, *, transport, parameters, endpoint=None, interface=None):
        ledger = getattr(self, '_native_route_ledger', None)
        if ledger is not None:
            ledger.observe(self, case, transport=transport, parameters=parameters,
                           endpoint=endpoint, interface=interface)

    def native_http_request(self, case, callback, endpoint, **kwargs):
        """Observe exact transport arguments before a fixed-native HTTP call."""
        ledger = getattr(self, '_native_route_ledger', None)
        if ledger is not None:
            from copy import deepcopy
            from ..native_routes import _digest
            body = deepcopy(kwargs.get('json_body'))
            if not isinstance(body, dict):
                ledger.reject(self, case, 'Native HTTP request lacks its declared input field')
            slots = [key for key in ('url', 'urls') if key in body]
            if slots == ['url'] and body['url'] == case.url:
                body['url'] = '<input-url>'
            elif slots == ['urls'] and body['urls'] == [case.url]:
                body['urls'] = ['<input-url>']
            else:
                ledger.reject(self, case, 'Native HTTP input differs from the frozen task URL')
            self.capture_native_request(case, transport='http', endpoint=endpoint,
                parameters={'method': kwargs.get('method', 'GET'), 'body': body,
                            'headers_sha256': _digest(kwargs.get('headers', {})),
                            'timeout_seconds': kwargs.get('timeout'),
                            'max_bytes': kwargs.get('max_bytes')})
        return callback(endpoint, **kwargs)

    @property
    def min_interval_seconds(self) -> float:
        """Minimum cooldown from one response completion to the next request start."""
        return max(0.0, float(self.config.get("min_interval_seconds", 0)))

    @abstractmethod
    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        raise NotImplementedError

    def validate(self) -> list[str]:
        return []

    def cancel(self) -> None:
        """Best-effort cancellation; non-cancellable calls are quarantined."""

    @property
    def cancellation_cleanup_seconds(self) -> float:
        """Bounded post-deadline grace for a cancellable call to stop.

        This is cleanup time, not answer latency. Adapters must opt in only
        when ``cancel`` actively stops their underlying work.
        """
        return 0.0

    def begin_call(self) -> None:
        """Reset cancellation state before the timed worker can start."""


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
