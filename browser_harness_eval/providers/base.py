from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import EvalCase, ProviderResult


class Provider(ABC):
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = config
        self.type = str(config.get("type", name))

    @abstractmethod
    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        raise NotImplementedError

    def validate(self) -> list[str]:
        return []


def build_provider(name: str, config: dict[str, Any]) -> Provider:
    provider_type = config.get("type")
    if provider_type == "http":
        from .http import HttpProvider

        return HttpProvider(name, config)
    if provider_type == "lexmount":
        from .lexmount import LexmountProvider

        return LexmountProvider(name, config)
    if provider_type == "firecrawl":
        from .firecrawl import FirecrawlProvider

        return FirecrawlProvider(name, config)
    if provider_type == "jina_reader":
        from .jina import JinaReaderProvider

        return JinaReaderProvider(name, config)
    if provider_type == "fixture":
        from .fixture import FixtureProvider

        return FixtureProvider(name, config)
    raise ValueError(f"unknown provider type: {provider_type!r}")
