from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from ..models import EvalCase, ProviderResult
from .base import Provider
from .http_client import request


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.skip = 0
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        self.parts.append(data)
        if self.in_title:
            self.title_parts.append(data)

    def text(self) -> str:
        value = html.unescape(" ".join(self.parts))
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n+", "\n", value)
        return value.strip()

    def title(self) -> str | None:
        value = " ".join(self.title_parts).strip()
        return value or None


class HttpProvider(Provider):
    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        response = request(
            case.url,
            timeout=timeout,
            max_bytes=int(self.config.get("max_bytes", 10_000_000)),
            headers={"Accept": "text/html,text/plain,application/xhtml+xml"},
        )
        content_type = response.headers.get("content-type", "")
        raw = response.text()
        title = None
        content = raw
        if "html" in content_type or "<html" in raw[:500].casefold():
            parser = TextExtractor()
            parser.feed(raw)
            content = parser.text()
            title = parser.title()
        ok = 200 <= response.status < 300
        return ProviderResult(
            ok=ok,
            content=content,
            status_code=response.status,
            final_url=response.url,
            title=title,
            error_type=None if ok else "http_error",
            error_message=None if ok else f"HTTP {response.status}",
            metadata={"content_type": content_type, "bytes": len(response.body)},
        )
