"""Narrow fixes for proven WebArena-Verified evaluator defects.

Keep these fixes local to replay evaluation until the pinned upstream package
contains the same behavior. They change normalization, never task answers.
"""

from __future__ import annotations

import contextlib
from typing import Any

from webarena_verified.core.evaluation.data_types import TYPE_REGISTRY, MarkdownString


class URLAwareMarkdownString(MarkdownString):
    """Normalize deployment URLs inside Markdown before structural comparison."""

    def _normalize_string(self, value: str) -> str:
        if value and "http" in value and self.derender_url_fct is not None:
            with contextlib.suppress(Exception):
                # NetworkEventEvaluator passes a partial that already binds
                # ``strict``. Calling it with a second strict argument makes the
                # upstream best-effort URL normalization a silent no-op.
                value = self.derender_url_fct(value)
        return super()._normalize_string(value)


def install_webarena_evaluator_fixes() -> None:
    """Install the URL-aware Markdown type in the upstream type registry."""

    current: Any = TYPE_REGISTRY.get("markdown")
    if current is not URLAwareMarkdownString:
        TYPE_REGISTRY["markdown"] = URLAwareMarkdownString
