"""Offline content-location audit; never execute JS, fetch URLs, or write files."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any

from lexbench_webfetch.metrics import fact_in_text, normalize_for_match
from lexbench_webfetch.models import EvalCase


class _InitialText(HTMLParser):
    """Conservative static DOM proxy, NOT a computed browser visibility check."""

    _excluded = {"head", "script", "style", "template", "noscript"}
    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.visible: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        style = re.sub(r"\s+", "", attributes.get("style") or "").lower()
        hidden = (
            bool(self.stack and self.stack[-1][1]) or tag in self._excluded
            or "hidden" in attributes or (attributes.get("aria-hidden") or "").lower() == "true"
            or "display:none" in style or "visibility:hidden" in style
        )
        if tag not in self._void:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if not self.stack or not self.stack[-1][1]:
            self.visible.append(data)


def _initial_visible(html: str) -> str:
    parser = _InitialText()
    parser.feed(html)
    parser.close()
    return " ".join(parser.visible)


def _payload_candidates(html: str) -> str:
    # Recover JSON string literals, including Unicode-escaped inline state/HTML.
    # No eval/unicode_escape, script execution, or external script requests.
    # Arbitrary JS expressions, compression and encodings remain undetected.
    candidates = [html]
    for match in re.finditer(r'"(?:[^"\\]|\\.)*"', html):
        try:
            value = json.loads(match[0])
        except ValueError:
            continue
        candidates.extend((value, _initial_visible(value)))
    return "\n".join(candidates)


def _coverage(text: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_for_match(text)
    groups = []
    for fact in facts:
        matched = [value for value in fact["alternatives"]
                   if fact_in_text(normalized, normalize_for_match(value))]
        groups.append({"id": fact["id"], "matched": matched, "pass": bool(matched)})
    hits = sum(group["pass"] for group in groups)
    return {"hits": hits, "total": len(groups),
            "coverage": hits / len(groups) if groups else None,
            "all_present": bool(groups) and hits == len(groups), "groups": groups}


def validate_render_reference(
    case: EvalCase | Mapping[str, Any], initial_html: str | bytes, rendered_text: str,
) -> dict[str, Any]:
    """Validate declared capture evidence, returning diagnostics and exact hashes.

    ``rendered_text`` must be independently captured visible text, not HTML,
    source textContent, provider output, or an abbreviated preview. Completion
    metadata lives in source_benchmark.render_evidence (see package README).
    ``valid`` means these mechanical gates pass, not proof of JS execution.
    """
    get = case.get if isinstance(case, Mapping) else lambda key, default=None: getattr(case, key, default)
    raw = initial_html.encode("utf-8") if isinstance(initial_html, str) else initial_html
    html = raw.decode("utf-8")
    reference = get("ground_truth_main_content", "") or ""
    errors = []
    facts = []
    seen = set()
    for index, fact in enumerate(get("required_facts", []) or []):
        identifier = str(fact.get("id") or f"fact-{index + 1}")
        alternatives = fact.get("alternatives", [])
        if not isinstance(alternatives, list):
            alternatives = []
        alternatives = [value for value in alternatives
                        if isinstance(value, str) and normalize_for_match(value)]
        if not alternatives:
            errors.append(f"empty_fact_group:{identifier}")
        if identifier in seen:
            errors.append(f"duplicate_fact_id:{identifier}")
        seen.add(identifier)
        facts.append({"id": identifier, "alternatives": alternatives})
    body_requirements = get("body_requirements", {}) or {}
    required_patterns = get("required_patterns", []) or []
    if not facts and not (body_requirements or required_patterns or get("acceptance_contract")):
        errors.append("required_facts_empty")
    texts = {"initial_visible": _initial_visible(html),
             "initial_payload": _payload_candidates(html),
             "rendered": rendered_text, "reference": reference}
    coverage = {key: _coverage(text, facts) for key, text in texts.items()}
    # Union per group: a page can have some facts in DOM and others in state.
    initial_hits = sum(a["pass"] or b["pass"] for a, b in zip(
        coverage["initial_visible"]["groups"], coverage["initial_payload"]["groups"]))
    if not facts:
        classification = "unclassified"
    elif coverage["initial_visible"]["all_present"]:
        classification = "initial_visible_content"
    elif initial_hits == len(facts):
        classification = "embedded_payload"
    elif initial_hits:
        classification = "partial_initial_content"
    else:
        classification = "absent_from_initial_evidence"
    if not raw.strip():
        errors.append("initial_html_empty")
    minimum = max(1, get("min_content_chars", 1))
    for key in ("reference", "rendered"):
        if not texts[key].strip():
            errors.append(f"{key}_empty")
        if len(texts[key].strip()) < minimum:
            errors.append(f"{key}_below_min_content_chars")
        for group in coverage[key]["groups"]:
            if not group["pass"]:
                errors.append(f"{key}_missing_fact:{group['id']}")
    if body_requirements or required_patterns:
        from lexbench_webfetch.metrics import evaluate_content
        for key in ("reference", "rendered"):
            checks = evaluate_content(texts[key], [], body_requirements=body_requirements,
                                      required_patterns=required_patterns)
            if not checks["body_coverage_pass"]:
                errors.append(f"{key}_body_requirements_not_met")
            if not all(row['pass'] for row in checks['pattern_results']):
                errors.append(f"{key}_pattern_requirements_not_met")
    evidence = (get("source_benchmark", {}) or {}).get("render_evidence", {})
    completion = evidence.get("completion", {})
    condition = completion.get("condition")
    timeout = completion.get("timeout_ms")
    if not isinstance(condition, str) or not condition.strip():
        errors.append("completion_condition_missing")
    bounded_timeout = (isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
                       and math.isfinite(timeout) and timeout > 0)
    if not bounded_timeout:
        errors.append("completion_timeout_not_bounded")
    if "elapsed_ms" in completion:
        elapsed = completion["elapsed_ms"]
        if (not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
                or not math.isfinite(elapsed) or elapsed < 0):
            errors.append("completion_elapsed_invalid")
        elif bounded_timeout and elapsed > timeout:
            errors.append("completion_deadline_exceeded")
    if completion.get("satisfied") is not True:
        errors.append("completion_not_satisfied")
    if evidence.get("rendered_truncated") is not False:
        errors.append("rendered_truncation_not_ruled_out")
    if not isinstance(evidence.get("scope"), str) or not evidence["scope"].strip():
        errors.append("reference_scope_missing")
    return {
        "case_id": get("id"), "valid": not errors, "errors": errors,
        "classification": classification, "coverage": coverage,
        "initial_fact_hits": initial_hits,
        "initial_html_sha256": hashlib.sha256(raw).hexdigest(),
        "rendered_text_sha256": hashlib.sha256(rendered_text.encode("utf-8")).hexdigest(),
        "reference_sha256": hashlib.sha256(reference.encode("utf-8")).hexdigest(),
        "completion": dict(completion), "js_required_proven": False,
        "limitations": [
            "Initial visible content is a static HTML proxy, not computed CSS visibility.",
            "Payload matching detects literals, not every encoding or external resource.",
            "Inline payload availability forbids a must-execute-JS conclusion; absence "
            "only means these facts were not found in this initial response.",
            "Fact coverage and declared completion do not prove whole-page completeness.",
        ],
    }
