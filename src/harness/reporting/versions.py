"""Identify report-building software without changing measured inputs."""

import hashlib
import json
import posixpath
import re
import subprocess
from functools import cache
from html import escape, unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from harness.distribution import repository_root
from harness.web_fetch import __version__

FILENAME = "browser-eval-version.json"
_RUNTIME_SUFFIXES = {".py", ".js", ".cjs", ".mjs", ".mts", ".c", ".json",
                     ".html", ".css", ".ini", ".txt", ".png"}


def _version_href(route, content):
    base = re.search(r'<base\b[^>]*\bhref=["\']([^"\']+)', content)
    document = 'https://report.invalid/' + route
    resolved = urljoin(document, unescape(base[1])) if base else document
    directory = posixpath.dirname(urlsplit(resolved).path)
    return posixpath.relpath('/' + FILENAME, directory or '/')


@cache
def software_identity():
    """Use the package's checkout, never an unrelated working directory."""
    package = Path(__file__).resolve().parents[1]
    root = repository_root(package)
    revision = None
    modified = False
    if (root / ".git").exists():
        try:
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            modified = bool(subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=normal", "--", str(package.relative_to(root))],
                cwd=root, text=True, stderr=subprocess.DEVNULL,
            ).strip())
        except (OSError, subprocess.CalledProcessError):
            revision = None
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if (not path.is_file() or path.suffix not in _RUNTIME_SUFFIXES
                or "__pycache__" in path.relative_to(package).parts):
            continue
        digest.update(path.relative_to(package).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return {"version": __version__, "revision": revision,
            "source_sha256": digest.hexdigest(), "modified": modified}


def bind_build_version(root):
    """Bind this build and add one compact link to each generated home page."""
    identity = {"role": "report_builder", **software_identity()}
    raw = (json.dumps(identity, indent=2) + "\n").encode()
    (root / FILENAME).write_bytes(raw)
    for route in ("index.html", "en/index.html"):
        page = root / route
        if not page.is_file():
            continue
        content = page.read_text()
        if "data-english-not-generated" in content:
            continue
        content = re.sub(r'<(p|span) data-report-build-version>.*?</\1>', '', content, flags=re.S)
        href = _version_href(route, content)
        link = ('<a data-browser-eval-release="'
                 + escape(identity["version"]) + '" href="' + href + '">Browser Eval v'
                 + escape(identity["version"]) + '</a>')
        benchmark = re.search(r'<a\b[^>]*\bdata-benchmark-release=[^>]*>.*?</a>', content, re.S)
        if benchmark:
            label = '<span data-report-build-version>' + link + ' · </span>'
            page.write_text(content[:benchmark.start()] + label + content[benchmark.start():])
            continue
        label = '<p data-report-build-version>' + link + '</p>'
        end = '</footer>' if '</footer>' in content else '</body>'
        if end not in content:
            raise ValueError("Report home has no body: " + route)
        page.write_text(content.replace(end, label + end, 1))
    return {"file": FILENAME, "sha256": hashlib.sha256(raw).hexdigest(),
            "version": identity["version"]}


def check_build_version(root, manifest):
    """Keep legacy reports readable; verify every declared build and label."""

    binding = manifest.get("browser_eval")
    path = root / FILENAME
    pages = [(route, BeautifulSoup((root / route).read_text(), "html.parser"))
             for route in ("index.html", "en/index.html") if (root / route).is_file()]
    if binding is None:
        if path.exists() or any(page.select_one('[data-browser-eval-release]') for _, page in pages):
            raise ValueError("Report lost its Browser Eval version binding")
        return
    if path.is_symlink() or binding.get("file") != FILENAME or not path.is_file():
        raise ValueError("Report Browser Eval version is missing")
    raw = path.read_bytes()
    identity = json.loads(raw)
    if (hashlib.sha256(raw).hexdigest() != binding.get("sha256")
            or identity.get("role") != "report_builder"
            or identity.get("version") != binding.get("version")
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", identity.get("version", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", identity.get("source_sha256", ""))
            or (identity.get("revision") is not None
                and not re.fullmatch(r"[0-9a-f]{40}", identity["revision"]))):
        raise ValueError("Report Browser Eval version changed")
    for route, page in pages:
        if page.select_one('[data-english-not-generated]'):
            continue
        labels = page.select('[data-browser-eval-release]')
        if (len(labels) != 1 or labels[0].get("data-browser-eval-release") != identity["version"]
                or labels[0].get_text(" ", strip=True) != "Browser Eval v" + identity["version"]
                or labels[0].get("href") != _version_href(route, str(page))):
            raise ValueError("Report lost its Browser Eval version label: " + route)


def record_build_version(root):
    """Stamp builders whose report manifest is already complete."""
    path = root / "report-manifest.json"
    manifest = json.loads(path.read_text()) if path.is_file() else {"schema_version": 1}
    manifest["browser_eval"] = bind_build_version(root)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
