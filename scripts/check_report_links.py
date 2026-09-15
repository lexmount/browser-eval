#!/usr/bin/env python3
from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs,unquote, urlsplit


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []
        self.ids: set[str] = set()
        self.filter_options: dict[str,set[str]] = {}
        self.current_filter: str | None = None
        self.base_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_by_name=dict(attrs)
        if tag == "base":
            self.base_href = attrs_by_name.get("href")
            return
        if tag=='select':
            self.current_filter=attrs_by_name.get('data-wmb-filter')
            if self.current_filter:self.filter_options.setdefault(self.current_filter,set())
        elif tag=='option' and self.current_filter:
            self.filter_options[self.current_filter].add(attrs_by_name.get('value') or '')
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.values.append(value)
            if key == "id" and value:
                self.ids.add(value)

    def handle_endtag(self,tag:str)->None:
        if tag=='select':self.current_filter=None


def check(root: Path) -> None:
    root = root.resolve()
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
                if parsed.fragment.startswith('inspect?'):
                    controls=parse_page(target).filter_options
                    params=parse_qs(parsed.fragment[8:],keep_blank_values=True)
                    if not controls or any(key not in controls or len(values)!=1 or values[0] not in controls[key]
                                           for key,values in params.items()):
                        missing.append(f"{page.relative_to(root)} -> {value} (invalid report filter)")
                    continue
                fragment = unquote(parsed.fragment)
                if fragment not in parse_page(target).ids:
                    missing.append(f"{page.relative_to(root)} -> {value} (missing fragment)")
    if missing:
        raise ValueError("Broken local links:\n" + "\n".join(missing))
    print(f"Report link check: {len(pages)} logical report routes, 0 broken local links")


def main() -> int:
    try:
        check(Path(sys.argv[1]))
    except ValueError as error:
        print(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
