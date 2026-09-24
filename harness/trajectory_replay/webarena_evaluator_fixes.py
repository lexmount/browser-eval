"""Narrow fixes for proven WebArena-Verified evaluator defects.

Keep these fixes local to replay evaluation until the pinned upstream package
contains the same behavior. They change normalization, never task answers.
"""

from __future__ import annotations

import contextlib
import re
from types import MappingProxyType
from typing import Any

from webarena_verified.core.evaluation.data_types import (
    TYPE_REGISTRY,
    MarkdownString,
    NormalizedString,
)
from webarena_verified.core.evaluation.evaluators.agent_response_evaluator import (
    AgentResponseEvaluator,
)
from webarena_verified.core.evaluation.evaluators.network_event_evaluator import (
    NetworkEventEvaluator,
)

_PLACEHOLDER = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")
_REPEATED_NETWORK_EVENT_TASKS = frozenset({567, 568, 569, 570})


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


def _template_match(template: str, actual: Any, bindings: dict[str, str]) -> bool:
    """Bind named placeholders while requiring every static character to match."""

    if not isinstance(actual, (str, int, float)):
        return False
    cursor = 0
    parts = ["^"]
    local_groups: set[str] = set()
    for match in _PLACEHOLDER.finditer(template):
        parts.append(re.escape(template[cursor : match.start()]))
        name = match.group(1)
        if name in bindings:
            parts.append(re.escape(bindings[name]))
        elif name in local_groups:
            parts.append(f"(?P={name})")
        else:
            parts.append(f"(?P<{name}>[^/?#&]+)")
            local_groups.add(name)
        cursor = match.end()
    parts.extend((re.escape(template[cursor:]), "$"))
    matched = re.fullmatch("".join(parts), str(actual))
    if matched is None:
        return False
    for name, value in matched.groupdict().items():
        if name in bindings and bindings[name] != value:
            return False
        bindings[name] = value
    return True


def _substitute_placeholders(value: Any, bindings: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda match: bindings[match.group(1)], value)
    if isinstance(value, list):
        return [_substitute_placeholders(item, bindings) for item in value]
    if isinstance(value, tuple):
        return tuple(_substitute_placeholders(item, bindings) for item in value)
    if isinstance(value, dict):
        return {
            _substitute_placeholders(key, bindings): _substitute_placeholders(item, bindings)
            for key, item in value.items()
        }
    return value


def _runtime_configs(context: Any, config: Any) -> list[Any]:
    """Resolve official dynamic fields from one candidate network event.

    The resolved contract is still evaluated by the upstream evaluator.  A
    placeholder repeated in a URL, form key, or form value must bind to the
    same non-empty value in that event.
    """

    expected = config.expected.model_dump(mode="python", exclude_none=False)
    serialized = repr(expected)
    names = set(_PLACEHOLDER.findall(serialized))
    candidates: list[Any] = []
    if names:
        urls = expected.get("url")
        url_templates = list(urls) if isinstance(urls, (list, tuple)) else [urls]
        post_templates = expected.get("post_data") or {}
        for event in context.network_trace.evaluation_events:
            if (
                expected.get("http_method")
                and event.http_method.lower() != expected["http_method"].lower()
            ):
                continue
            bindings: dict[str, str] = {}
            dynamic_urls = [
                item
                for item in url_templates
                if isinstance(item, str) and _PLACEHOLDER.search(item)
            ]
            if dynamic_urls:
                actual_url = context.config.derender_url(
                    event.url, sites=context.task.sites, strict=False
                )
                if not any(_template_match(item, actual_url, bindings) for item in dynamic_urls):
                    continue
            actual_post = dict(event.post_data or {})
            valid = True
            for key_template, value_template in post_templates.items():
                if _PLACEHOLDER.search(key_template):
                    matched_key = next(
                        (
                            key
                            for key in actual_post
                            if _template_match(key_template, key, bindings.copy())
                        ),
                        None,
                    )
                    if matched_key is None or not _template_match(
                        key_template, matched_key, bindings
                    ):
                        valid = False
                        break
                    actual_value = actual_post[matched_key]
                else:
                    actual_value = actual_post.get(key_template)
                if isinstance(value_template, str) and _PLACEHOLDER.search(value_template):
                    if not _template_match(value_template, actual_value, bindings):
                        valid = False
                        break
            if not valid or set(bindings) != names:
                continue
            corrected_expected = _substitute_placeholders(expected, bindings)
            candidates.append(
                config.model_copy(
                    update={"expected": config.expected.model_copy(update=corrected_expected)}
                )
            )
    else:
        candidates = [config]

    corrected = []
    for candidate in candidates:
        post_data = candidate.expected.post_data or {}
        singleton_arrays = {
            key
            for key, value in post_data.items()
            if isinstance(value, (list, tuple)) and len(value) == 1
        }
        if singleton_arrays and not candidate.post_data_schema:
            schema = {
                "type": "object",
                "properties": {
                    key: {"type": "array", "items": {"type": "string"}} for key in singleton_arrays
                },
            }
            candidate = candidate.model_copy(update={"post_data_schema": schema})
        corrected.append(candidate)
    return corrected


def install_webarena_evaluator_fixes() -> None:
    """Install the URL-aware Markdown type in the upstream type registry."""

    if TYPE_REGISTRY.get("string") is not URLAwareNormalizedString:
        TYPE_REGISTRY["string"] = URLAwareNormalizedString
    if TYPE_REGISTRY.get("markdown") is not URLAwareMarkdownString:
        TYPE_REGISTRY["markdown"] = URLAwareMarkdownString
    if not getattr(
        AgentResponseEvaluator._normalized_actual_value,
        "_lex_null_retrieved_data_fix",
        False,
    ):
        original_normalize_actual = AgentResponseEvaluator._normalized_actual_value

        def _normalized_actual_value(
            self: Any,
            actual_raw: Any,
            normalized_expected: Any,
            config: Any,
            context: Any,
        ) -> Any:
            normalized = original_normalize_actual(
                self, actual_raw, normalized_expected, config, context
            )
            if (
                isinstance(normalized, (dict, MappingProxyType))
                and "retrieved_data" in normalized_expected
                and "retrieved_data" not in normalized
            ):
                actual = self._get_actual_agent_response_dict(actual_raw)
                raw_retrieved = actual.get("retrieved_data") if isinstance(actual, dict) else None
                normalized_retrieved = (
                    self._normalized_retrieved_data(
                        retrieved_data_raw=raw_retrieved,
                        strict=False,
                        context=context,
                        config=config,
                    )
                    if context.task.is_retrieve_task
                    else None
                )
                normalized = MappingProxyType(
                    {**normalized, "retrieved_data": normalized_retrieved}
                )
            return normalized

        _normalized_actual_value._lex_null_retrieved_data_fix = True  # type: ignore[attr-defined]
        AgentResponseEvaluator._normalized_actual_value = _normalized_actual_value
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
            if (
                context.task.task_id in _REPEATED_NETWORK_EVENT_TASKS
                and config.last_event_only
                and len(siblings) > 1
            ):
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
    if not getattr(NetworkEventEvaluator.evaluate, "_lex_dynamic_contract_fix", False):
        original_evaluate = NetworkEventEvaluator.evaluate

        def evaluate(self: Any, *, context: Any, config: Any) -> Any:
            candidates = _runtime_configs(context, config)
            if not candidates:
                return original_evaluate(self, context=context, config=config)
            first = None
            for candidate in candidates:
                result = original_evaluate(self, context=context, config=candidate)
                first = first or result
                if result.score == 1:
                    return result
            return first

        evaluate._lex_dynamic_contract_fix = True  # type: ignore[attr-defined]
        NetworkEventEvaluator.evaluate = evaluate
