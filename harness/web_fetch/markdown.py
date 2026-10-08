"""One pinned HTML-to-Markdown converter shared by browser adapters."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

CONVERTER = Path(__file__).with_name('html_to_markdown.mjs')
DEFAULT_PACKAGE = Path(__file__).resolve().parents[2] / 'node_modules/node-html-markdown'


def converter_package(configured: str | None = None) -> Path:
    return Path(configured).expanduser().resolve() if configured else DEFAULT_PACKAGE


def require_converter(configured: str | None = None) -> Path:
    package = converter_package(configured)
    if not shutil.which('node') or not CONVERTER.is_file():
        raise ValueError('HTML conversion requires Node.js and the packaged converter')
    try:
        version = json.loads((package / 'package.json').read_text())['version']
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError('HTML conversion requires node-html-markdown 2.0.0; set node_html_markdown_path') from error
    if version != '2.0.0':
        raise ValueError('HTML conversion requires node-html-markdown 2.0.0')
    return package


def converter_command(package: Path) -> list[str]:
    return [shutil.which('node') or 'node', str(CONVERTER), str(package)]
