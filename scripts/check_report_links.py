#!/usr/bin/env python3
from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.values.append(value)


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    missing: list[str] = []
    pages = list(root.rglob("*.html"))
    for page in pages:
        parser = Links()
        parser.feed(page.read_text(encoding="utf-8", errors="replace"))
        for value in parser.values:
            parsed = urlsplit(value)
            if parsed.scheme or value.startswith(("#", "mailto:")):
                continue
            target = (page.parent / unquote(parsed.path)).resolve()
            if not target.exists():
                missing.append(f"{page.relative_to(root)} -> {value}")
    if missing:
        print("Broken local links:")
        print("\n".join(missing))
        return 1
    print(f"Report link check: {len(pages)} HTML pages, 0 broken local links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
