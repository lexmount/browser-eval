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
        self.ids: set[str] = set()
        self.base_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "base":
            self.base_href = next((value for key, value in attrs if key == "href" and value), None)
            return
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.values.append(value)
            if key == "id" and value:
                self.ids.add(value)


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    missing: list[str] = []
    pages = list(root.rglob("*.html"))
    parsed_pages: dict[Path, Links] = {}

    def parse_page(path: Path) -> Links:
        if path not in parsed_pages:
            parsed_pages[path] = Links()
            parsed_pages[path].feed(path.read_text(encoding="utf-8", errors="replace"))
        return parsed_pages[path]

    for page in pages:
        parser = parse_page(page)
        base_dir = page.parent
        if parser.base_href:
            parsed_base = urlsplit(parser.base_href)
            if not parsed_base.scheme:
                base_dir = (page.parent / unquote(parsed_base.path)).resolve()
        for value in parser.values:
            parsed = urlsplit(value)
            if parsed.scheme or value.startswith("mailto:"):
                continue
            target = page if not parsed.path else (base_dir / unquote(parsed.path)).resolve()
            if not target.exists():
                missing.append(f"{page.relative_to(root)} -> {value}")
                continue
            if parsed.fragment and target.suffix.lower() in {".html", ".htm"}:
                fragment = unquote(parsed.fragment)
                if fragment not in parse_page(target).ids:
                    missing.append(f"{page.relative_to(root)} -> {value} (missing fragment)")
    if missing:
        print("Broken local links:")
        print("\n".join(missing))
        return 1
    print(f"Report link check: {len(pages)} logical report routes, 0 broken local links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
