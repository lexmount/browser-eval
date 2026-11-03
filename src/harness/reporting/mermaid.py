"""The one Mermaid runtime that report pages load from a CDN."""

import re

MERMAID_VERSION = "11.4.1"
MERMAID_URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
MERMAID_INTEGRITY = "sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I"

_MERMAID_URL_PATTERN = re.compile(r"""(?:https?:)?//[^\s'"`<>()/]+/[^\s'"`<>()]*mermaid[^\s'"`<>()]*""", re.I)


def mermaid_script_attributes(element: str) -> str:
    """Return the JavaScript that points the script variable ``element`` at the pinned runtime."""
    return (
        f"{element}.src='{MERMAID_URL}';"
        f"{element}.integrity='{MERMAID_INTEGRITY}';"
        f"{element}.crossOrigin='anonymous'"
    )


def mermaid_urls(text: str) -> list[str]:
    """Return every URL in ``text`` whose path names Mermaid."""
    return _MERMAID_URL_PATTERN.findall(text)


def unpinned_mermaid_urls(text: str) -> list[str]:
    """Return every Mermaid URL in ``text`` other than the pinned runtime."""
    return [url for url in mermaid_urls(text) if url != MERMAID_URL]
