"""Narrow fixes for proven WebArena-Verified evaluator defects.

Keep these fixes local to replay evaluation until the pinned upstream package
contains the same behavior. They change normalization, never task answers.
"""

from __future__ import annotations

import contextlib
from typing import Any

from webarena_verified.core.evaluation.data_types import (
    TYPE_REGISTRY,
    MarkdownString,
    NormalizedString,
)
from webarena_verified.core.evaluation.evaluators.network_event_evaluator import (
    NetworkEventEvaluator,
)


def _derender_embedded_url(instance: Any, value: str) -> str:
    if value and "http" in value and instance.derender_url_fct is not None:
        with contextlib.suppress(Exception):
            # NetworkEventEvaluator passes a partial that already binds
            # ``strict``. Calling it with a second strict argument makes the
            # upstream best-effort URL normalization a silent no-op.
            return instance.derender_url_fct(value)
    return value


class URLAwareNormalizedString(NormalizedString):
    """Preserve upstream string normalization after URL derendering."""

    def _normalize_string(self, value: str) -> str:
        return super()._normalize_string(_derender_embedded_url(self, value))


class URLAwareMarkdownString(MarkdownString):
    """Normalize deployment URLs inside Markdown before structural comparison."""

    def _normalize_string(self, value: str) -> str:
        return super()._normalize_string(_derender_embedded_url(self, value))


def install_webarena_evaluator_fixes() -> None:
    """Install the URL-aware Markdown type in the upstream type registry."""

    if TYPE_REGISTRY.get("string") is not URLAwareNormalizedString:
        TYPE_REGISTRY["string"] = URLAwareNormalizedString
    if TYPE_REGISTRY.get("markdown") is not URLAwareMarkdownString:
        TYPE_REGISTRY["markdown"] = URLAwareMarkdownString
    if not getattr(NetworkEventEvaluator._get_actual_value, "_lex_duplicate_url_fix", False):
        original = NetworkEventEvaluator._get_actual_value

        def _get_actual_value(self: Any, context: Any, config: Any) -> Any:
            siblings = [
                item
                for item in context.task.eval
                if item.evaluator == "NetworkEventEvaluator"
                and item.expected.url == config.expected.url
                and item.expected.http_method == config.expected.http_method
            ]
            if config.last_event_only and len(siblings) > 1:
                # Upstream defaults to the final URL/method match before it
                # compares POST data.  Official tasks that intentionally send
                # several distinct bodies to one endpoint therefore make every
                # assertion except the last impossible.  Preserve the complete
                # candidate list only for that proven duplicate-contract case;
                # the upstream comparator already accepts any full match.
                config = config.model_copy(update={"last_event_only": False})
            return original(self, context, config)

        _get_actual_value._lex_duplicate_url_fix = True  # type: ignore[attr-defined]
        NetworkEventEvaluator._get_actual_value = _get_actual_value
