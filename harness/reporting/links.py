#!/usr/bin/env python3
from __future__ import annotations

import argparse
import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.links = []
        self.evidence = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "img" and values.get("src"):
            self.images.append(values["src"])
        if values.get("href"):
            self.links.append(values["href"])
        if values.get("data-file"):
            self.evidence.append(values["data-file"])


def frozen_source_html(name, content):
    """Captured site navigation is evidence; authored report navigation is checked."""
    if isinstance(content, bytes):
        content = content.decode('utf-8',errors='replace')
    return ('local-evidence' in Path(name).parts and not re.search(
        r'<meta\b[^>]*name=[\'"]browser-eval-report[\'"]',content))


def validate_virtual_links(files, read):
    """Check the complete inventory after privacy projection or an overlay."""
    for name, file in files.items():
        if file["mime"] != "text/html":
            continue
        content = read(name)
        if frozen_source_html(name,content):
            continue
        parsed = PageLinks()
        parsed.feed(content.decode("utf-8") if isinstance(content, bytes) else content)
        for image, urls in ((False, parsed.links), (True, parsed.images)):
            for url in urls:
                parts = urlsplit(url)
                if parts.scheme or parts.netloc:
                    continue
                target = (posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parts.path)))
                          if parts.path else name)
                if target not in files or (image and not files[target]["mime"].startswith("image/")):
                    raise ValueError(f"Invalid internal {'image' if image else 'link'}: {name}: {url}")
        for target in parsed.evidence:
            if target not in files:
                raise ValueError(f"Invalid internal evidence: {name}: {target}")


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []
        self.ids: set[str] = set()
        self.filter_options: dict[str,set[str]] = {}
        self.text_filters: set[str] = set()
        self.current_filter: str | None = None
        self.base_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_by_name=dict(attrs)
        if tag == "base":
            self.base_href = attrs_by_name.get("href")
            return
        if tag=='select':
            self.current_filter=(attrs_by_name.get('data-wmb-filter') or attrs_by_name.get('data-filter'))
            if self.current_filter:self.filter_options.setdefault(self.current_filter,set())
        elif tag=='input' and (attrs_by_name.get('data-wmb-filter') or attrs_by_name.get('data-filter')):
            self.text_filters.add(attrs_by_name.get('data-wmb-filter') or attrs_by_name['data-filter'])
        elif tag=='option' and self.current_filter:
            self.filter_options[self.current_filter].add(attrs_by_name.get('value') or '')
        for key, value in attrs:
            if key in {"href", "src", "data-src"} and value:
                self.values.append(value)
            if key == "id" and value:
                self.ids.add(value)

    def handle_endtag(self,tag:str)->None:
        if tag=='select':self.current_filter=None


def check(root: Path) -> None:
    root = root.resolve()
    missing: list[str] = []
    pages = list(root.rglob("*.html"))
    if not root.is_dir() or not pages:
        raise ValueError("Report directory is missing or contains no HTML pages")
    parsed_pages: dict[Path, Links] = {}

    def parse_page(path: Path) -> Links:
        if path not in parsed_pages:
            parsed_pages[path] = Links()
            parsed_pages[path].feed(path.read_text(encoding="utf-8", errors="replace"))
        return parsed_pages[path]

    for page in pages:
        # Frozen source HTML retains the original site's relative navigation.
        # It is evidence, while report links pointing to it still need to exist.
        relative = page.relative_to(root)
        if frozen_source_html(relative,page.read_text(encoding="utf-8",errors="replace")):
            continue
        parser = parse_page(page)
        base_dir = root if (root / 'sample-manifest.json').is_file() else page.parent
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
                    target_page=parse_page(target)
                    controls=target_page.filter_options
                    params=parse_qs(parsed.fragment[8:],keep_blank_values=True)
                    if not controls or any((key not in controls and key not in target_page.text_filters) or len(values)!=1 or (key in controls and values[0] not in controls[key])
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
    parser = argparse.ArgumentParser(description="Check report links and fragment targets")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        check(args.report)
    except ValueError as error:
        print(error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
