"""Every report loads the one exact Mermaid runtime named by the shared owner."""

import re
from pathlib import Path

import pytest

from harness.reporting import mermaid
from harness.reporting.mermaid import (
    MERMAID_URL,
    mermaid_script_attributes,
    mermaid_urls,
    unpinned_mermaid_urls,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js",
        "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs",
        "https://cdn.jsdelivr.net/npm/mermaid@latest/dist/mermaid.min.js",
        "https://cdn.jsdelivr.net/npm/mermaid@11.4.0/dist/mermaid.min.js",
        "//cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js",
        "https://unpkg.com/mermaid@11.4.1/dist/mermaid.min.js",
    ],
)
def test_any_other_mermaid_url_is_rejected(url: str) -> None:
    assert unpinned_mermaid_urls(f"import mermaid from '{url}';") == [url]
    assert unpinned_mermaid_urls(f'<script src="{url}"></script>') == [url]


def test_pinned_runtime_and_unrelated_urls_are_accepted() -> None:
    page = f"<script src=\"{MERMAID_URL}\"></script><a href='https://cdn.jsdelivr.net/npm/chart.js'>x</a>"
    assert unpinned_mermaid_urls(page) == []


def test_owner_names_an_exact_version_and_applies_integrity() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", mermaid.MERMAID_VERSION)
    assert re.fullmatch(r"sha384-[A-Za-z0-9+/]{64}", mermaid.MERMAID_INTEGRITY)
    attributes = mermaid_script_attributes("loader")
    assert f"loader.src='{MERMAID_URL}'" in attributes
    assert f"loader.integrity='{mermaid.MERMAID_INTEGRITY}'" in attributes
    assert "loader.crossOrigin='anonymous'" in attributes


def test_only_the_owner_names_a_mermaid_url() -> None:
    owner = Path(mermaid.__file__).resolve()
    sources = [
        path
        for directory in ("src/harness", "scripts")
        for path in (ROOT / directory).rglob("*")
        if path.suffix in {".py", ".js", ".mjs", ".html"} and path.resolve() != owner
    ]
    assert sources
    offenders = {
        path.relative_to(ROOT).as_posix(): found
        for path in sources
        if (found := mermaid_urls(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}
