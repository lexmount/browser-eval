"""An explicitly supplied historical article, separate from current measurements."""
import hashlib
import json
import re
import shutil
import base64
from pathlib import Path
from urllib.parse import urlsplit

from .safety import contained_path


def sanitize_article_html(fragment):
    """Keep static article content; never import executable HTML or global CSS."""
    from bs4 import BeautifulSoup
    allowed = {'article', 'section', 'div', 'span', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
               'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'ul', 'ol', 'li', 'a', 'img',
               'figure', 'figcaption', 'pre', 'code', 'blockquote', 'strong', 'em', 'b', 'i',
               'small', 'sup', 'sub', 'br', 'hr', 'details', 'summary'}
    soup = BeautifulSoup(fragment, 'html.parser')
    for element in list(soup.find_all(['script', 'style', 'iframe', 'form', 'input', 'button',
                                     'object', 'embed', 'link', 'meta', 'base', 'svg', 'math', 'template'])):
        element.decompose()
    for element in list(soup.find_all(True)):
        if element.name not in allowed:
            element.unwrap()
            continue
        attrs = {}
        for key in ('title', 'alt', 'colspan', 'rowspan'):
            if key in element.attrs:
                attrs[key] = str(element.attrs[key])
        if element.get('id'):
            attrs['id'] = 'archived-' + str(element['id'])
        for key in ('href', 'src'):
            if key not in element.attrs or (key == 'src' and element.name != 'img'):
                continue
            value = str(element[key]).strip()
            compact = re.sub(r'[\x00-\x20\x7f]', '', value)
            if key == 'src' and compact.startswith('data:'):
                raster = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})', compact)
                if raster and len(raster[2]) <= 16_000_000:
                    try:
                        raw = base64.b64decode(raster[2], validate=True)
                    except ValueError:
                        raw = b''
                    kind = raster[1]
                    if ((kind == 'png' and raw.startswith(b'\x89PNG\r\n\x1a\n'))
                            or (kind == 'jpeg' and raw.startswith(b'\xff\xd8\xff'))
                            or (kind == 'webp' and raw.startswith(b'RIFF') and raw[8:12] == b'WEBP')):
                        attrs[key] = value
                continue
            try:
                parts = urlsplit(compact)
            except ValueError:
                continue
            if '\\' in compact or parts.scheme.lower() not in {'', 'http', 'https', 'mailto'}:
                continue
            if key == 'src' and parts.scheme == 'mailto':
                continue
            attrs[key] = '#archived-' + value[1:] if value.startswith('#') else value
        if element.name == 'a' and 'href' in attrs:
            attrs['rel'] = 'noopener noreferrer'
        element.attrs = attrs
    for table in soup.find_all('table'):
        table.wrap(soup.new_tag('div', attrs={'class': 'table-wrap'}))
    return str(soup)


def copy_archived_reading(source: Path, out: Path) -> None:
    manifest = source / "archived-reading.json"
    if not manifest.is_file():
        return
    data = json.loads(manifest.read_text())
    for item in [*data["languages"].values(), *([data["translations"]] if data.get("translations") else [])]:
        origin = contained_path(source, item["path"])
        if origin.is_symlink() or hashlib.sha256(origin.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Archived article content differs from its manifest")
        target = contained_path(out, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
    shutil.copy2(manifest, out / "archived-reading.json")


def render_archived_reading(out: Path, language: str = "zh") -> str | None:
    manifest = out / "archived-reading.json"
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text())
    item = data["languages"][language]
    raw = contained_path(out, item["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("Archived article hash mismatch")
    text = raw.decode()
    body = re.search(r"<main[^>]*>(.*?)</main>", text, re.S)
    if not body:
        raise ValueError("Archived article has no main content")
    date = data.get("source_data_date")
    if language == "zh":
        date_note = f"（截至{date}）" if date else ""
        note = f"本文沿用旧报告文章与原有数据{date_note}，不代表本轮评测结果。"
    else:
        date_note = f" through {date}" if date else ""
        note = f"This article retains the older report and its data{date_note}; it does not describe the current benchmark results."
    return ('<div class="wrap" data-archived-reading="true"><p class="source-note">' + note
            + '</p><div class="essay">' + sanitize_article_html(body[1]) + '</div></div>')


def archived_translations(out: Path) -> dict[str, str]:
    manifest = out / "archived-reading.json"
    if not manifest.is_file():
        return {}
    item = json.loads(manifest.read_text()).get("translations")
    if not item:
        return {}
    raw = contained_path(out, item["path"], reject_symlinks=True).read_bytes()
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("Archived translations hash mismatch")
    text = json.loads(raw)["text"]
    if not isinstance(text, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in text.items()):
        raise ValueError("Report translations require text pairs")
    return text
