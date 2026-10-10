"""Narrow bilingual parity normalization for manifest-bound article images."""

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from harness.web_fetch.safety import contained_path, safe_identifier


class ArticleAssetParity:
    def __init__(self, root, manifest):
        self.root = Path(root).resolve()
        self.figures = {}
        seen = set()
        for figure in json.loads(Path(manifest).read_text())["figures"]:
            slot = safe_identifier(figure["slot"])
            if slot in seen:
                raise ValueError("Duplicate article figure slot")
            seen.add(slot)
            assets = figure["assets"]
            if "en" not in assets:
                continue
            variants = {}
            for locale in ("zh", "en"):
                asset = assets[locale]
                path = contained_path(
                    self.root, Path("assets/editorial") / asset["file"], reject_symlinks=True
                )
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != asset["sha256"]:
                    raise ValueError(
                        "Localized article image differs from manifest: " + asset["file"]
                    )
                variants[locale] = {
                    str(path),
                    "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                }
            self.figures[figure["slot"]] = variants

    def rewrite(self, page, figure, tag, attrs):
        """Normalize only declared URLs inside their declared article figure."""
        page = Path(page).resolve()
        relative = page.relative_to(self.root).as_posix()
        if relative not in {
            "guide/index.html",
            "guide/article.html",
            "en/guide/index.html",
            "en/guide/article.html",
        }:
            return attrs
        variants = self.figures.get(figure)
        attribute = {"a": "href", "img": "src"}.get(tag)
        if not variants or not attribute:
            return attrs

        def identify(value):
            if not value:
                return None
            parsed = urlsplit(value)
            if not parsed.scheme and not parsed.netloc and not parsed.query and not parsed.fragment:
                value = str((page.parent / parsed.path).resolve())
            return next((locale for locale, known in variants.items() if value in known), None)

        values = dict(attrs)
        locale = identify(values.get(attribute))
        if locale != ("en" if relative.startswith("en/") else "zh"):
            return attrs
        alternate = "data-en-" + attribute
        valid_alternate = locale == "zh" and identify(values.get(alternate)) == "en"
        return [
            (name, "#editorial-asset-" + figure if name == attribute else value)
            for name, value in attrs
            if not (name == alternate and valid_alternate)
        ]
