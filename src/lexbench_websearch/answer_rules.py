"""Versioned literal answer evidence rules derived from reference annotations.

These rules recognize supplied alternatives and numeric tolerances, not semantic
correctness, entailment, or inferred synonyms. A match can occur in unrelated text.
"""

import re
import unicodedata
from decimal import Decimal

# Frozen input policy and algorithm receipt have separate version identities.
POLICY_NAME = "published-answers/2"
RULE_VERSION = "reference-answer-evidence/3"
NUMBER = r"(?:\+\s*|-)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_OPERATOR_TRANSLATION = str.maketrans(
    {"−": "-", "⁄": "/", "∕": "/", "⋅": "·", "∙": "·"}
)


def _norm(value):
    return (
        unicodedata.normalize("NFKC", value)
        .translate(_OPERATOR_TRANSLATION)
        .casefold()
        .strip()
    )


def _matching_text(value):
    # Match common answer formatting without changing the retained response.
    # A bare leading "- 150" remains a negative quantity; a bullet after a
    # preceding line is list formatting.
    value = re.sub(r"(?m)(?<=\n)([ \t]*)(?:[-+*]|\d+[.)])[ \t]+", r"\1", value)
    for _ in range(2):
        value = re.sub(
            r"(?<!\w)(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)",
            r"\2",
            value,
        )
    return _norm(value)


def _decimal(value):
    return Decimal(re.sub(r"\s+", "", value.replace(",", "")))


# A matched quantity/unit cannot end before a rate, percentage or exponent.
# NFKC normalizes superscript unit powers (m² -> m2), covered by the word guard.
_QUANTITY_OPERATORS = r"[/\\^*×÷·]"
_QUANTITY_END = (
    rf"(?![\w%‰]|{_QUANTITY_OPERATORS}|[.,]\d"
    rf"|\s*(?:[%‰]|{_QUANTITY_OPERATORS}|per\b)"
    r"|(?:\s*[)\]}])+(?:\d|\s*(?:[%‰]|"
    rf"{_QUANTITY_OPERATORS}|per\b)))"
)


def _leading_operator(value, start):
    index = start - 1
    while index >= 0 and (value[index].isspace() or value[index] in "([{"):
        index -= 1
    prefix = value[index] if index >= 0 else ""
    return prefix if re.fullmatch(_QUANTITY_OPERATORS + r"|[+-]", prefix) else ""


def _phrase(text, phrase, *, at_end=False):
    # Keep numeric modifiers attached to their number, so 150 != 150%.
    reference = _norm(phrase)
    pattern = NUMBER + r"(?:\s*[%‰])?|[^\W\d_]+|[♯♭]"
    if re.search(NUMBER, reference):
        pattern += "|" + _QUANTITY_OPERATORS + r"|[()\[\]{}]|_+"

    def token(value):
        modifier = re.fullmatch(r"(" + NUMBER + r")\s*([%‰]?)", value)
        return (
            str(_decimal(modifier[1]).normalize()) + modifier[2] if modifier else value
        )

    def attachment(value, match):
        prefix = value[match.start() - 1] if match.start() else ""
        return _leading_operator(value, match.start()) or (
            prefix if re.fullmatch(r"[\w.,+\-]", prefix) else ""
        )

    normalized = _matching_text(text)
    reference_matches = list(re.finditer(pattern, reference))
    numeric = [
        j
        for j, match in enumerate(reference_matches)
        if re.fullmatch(NUMBER + r"(?:\s*[%‰])?", match.group())
    ]
    matches = list(re.finditer(pattern, normalized))
    actual = [token(m.group()) for m in matches]
    wanted = [token(m.group()) for m in reference_matches]
    if not wanted:
        return False
    for i in range(len(actual) - len(wanted) + 1):
        if actual[i : i + len(wanted)] != wanted:
            continue
        if at_end and matches[i + len(wanted) - 1].end() != len(normalized):
            continue
        # Numeric references additionally retain complete quantities and units.
        if numeric:
            # Check every number, preserving attachments explicitly in the reference.
            if any(
                attachment(normalized, matches[i + j])
                not in ("", attachment(reference, reference_matches[j]))
                for j in numeric
            ):
                continue
            end = matches[i + len(wanted) - 1].end()
            if not re.match(_QUANTITY_END, normalized[end:]):
                continue
        return True
    return False


def _reference_text(reference, derivation=None):
    """Separate citation metadata while preserving answers that are URLs."""
    value = reference.strip()
    if derivation is None:
        return value
    if not re.match(r"https?://", value, re.I):
        fused = re.fullmatch(r"(.+[\w])https?://[^\s]+", value, re.I)
        if fused:
            value = fused[1].rstrip()
        citation = re.fullmatch(r"(.+?)\s*(?:\[\d+\])+", value)
        if citation and citation[1].strip():
            value = citation[1].rstrip()
    from urllib.parse import urlsplit

    sources = derivation.get("sources", [])
    if (
        derivation.get("reference") != reference
        or derivation.get("kind") != "terminal-citation-metadata"
        or value == reference.strip()
        or derivation.get("derived_answer") != value
        or not derivation.get("qualification")
        or not sources
        or not any(
            isinstance(source, dict)
            and urlsplit(source.get("url", "")).scheme == "https"
            and urlsplit(source.get("url", "")).netloc
            and _phrase(source.get("excerpt", ""), value)
            for source in sources
        )
    ):
        raise ValueError(
            "Answer derivation must bind the reference and independent source"
        )
    return value


def reference_rule(reference, *, derivation=None):
    """Return a JSON-serializable derivation; unknown annotations are explicit."""
    value = _reference_text(reference, derivation)
    rule = {
        "version": RULE_VERSION,
        "reference": reference,
        "kind": "literal",
        "alternatives": [value],
        "unsupported_annotation": None,
    }
    if value != reference.strip():
        rule["annotation_cleanup"] = {
            "derived_answer": value,
            "kind": "terminal-citation-metadata",
        }
    annotation = re.fullmatch(r"(.+?)\s*\(acceptable range:\s*(.+)\)", value, re.I)
    if annotation:
        base, body = annotation.groups()
        rule["alternatives"] = [base.strip()]
        # Preserve a fixed prefix for compound values such as years + days.
        span = re.fullmatch(
            r"(.*?)anything between\s+("
            + NUMBER
            + r")\s*(.*?)\s+and\s+("
            + NUMBER
            + r")\s*(.*)",
            body,
            re.I,
        )
        if span:
            prefix, low, low_unit, high, high_unit = span.groups()
            # A repeated scale may occur before a final currency (million USD).
            if low_unit and not _norm(high_unit).startswith(_norm(low_unit)):
                rule["unsupported_annotation"] = body
                return rule
            if _decimal(low) > _decimal(high):
                rule["unsupported_annotation"] = body
                return rule
            rule.update(
                kind="numeric_range",
                lower=str(_decimal(low)),
                upper=str(_decimal(high)),
                unit=high_unit.strip(),
                prefix=prefix.strip(),
            )
            return rule
        dimension = re.fullmatch(
            r"\[("
            + NUMBER
            + r")-("
            + NUMBER
            + r")\]\s*[x×]\s*\[("
            + NUMBER
            + r")-("
            + NUMBER
            + r")\]\s*(.+)",
            body,
        )
        if dimension:
            a, b, c, d, unit = dimension.groups()
            rule.update(
                kind="dimensions",
                ranges=[
                    [str(_decimal(a)), str(_decimal(b))],
                    [str(_decimal(c)), str(_decimal(d))],
                ],
                unit=unit.strip(),
            )
            return rule
        rule["unsupported_annotation"] = body
        return rule
    also = re.fullmatch(r"(.+?)\s*\(also know[n]? as\s+(.+)\)", value, re.I)
    if also:
        base, alternatives = also.groups()
        aliases = [v.strip().strip('"') for v in re.split(r"\s+or\s+", alternatives)]
        rule.update(kind="explicit_alternatives", alternatives=[base.strip()] + aliases)
        return rule
    terminal = re.fullmatch(r"(.+?)\s*\(or\s+([^()]+)\)", value, re.I)
    if terminal:
        rule.update(
            kind="explicit_alternatives",
            alternatives=[v.strip() for v in terminal.groups()],
        )
        return rule
    inline = re.search(r"([^\s,]+)\s*\(or\s+([^()]+)\)", value, re.I)
    if inline:
        first, second = inline.groups()
        rule.update(
            kind="explicit_alternatives",
            alternatives=[
                value[: inline.start()] + v + value[inline.end() :]
                for v in (first, second)
            ],
        )
        return rule
    if "(" in value or ")" in value:
        rule["unsupported_annotation"] = "Uninterpreted parentheses retained literally"
    return rule


def _unit_pattern(unit):
    return r"\s*".join(re.escape(p) for p in _norm(unit).split())


def _numeric_matches(text, unit):
    # A number must be complete: 150 must not be found inside 1,150 or 150.9.
    suffix = r"\s*" + _unit_pattern(unit) if unit else ""
    pattern = r"(?<![\w.,+\-])(" + NUMBER + r")" + suffix + _QUANTITY_END
    normalized = _matching_text(text)
    return (
        match
        for match in re.finditer(pattern, normalized)
        if not _leading_operator(normalized, match.start())
    )


def match_reference_answer(reference, text, *, derivation=None):
    """Whether text contains explicitly allowed reference evidence (not truth)."""
    rule = reference_rule(reference, derivation=derivation)
    if rule["kind"] == "numeric_range":
        low, high = Decimal(rule["lower"]), Decimal(rule["upper"])
        normalized = _matching_text(text)
        for match in _numeric_matches(text, rule["unit"]):
            if not low <= _decimal(match.group(1)) <= high:
                continue
            if rule["prefix"]:
                prefix = _norm(rule["prefix"]).rstrip(", ")
                before = normalized[: match.start()].rstrip(", ")
                if not _phrase(before, prefix, at_end=True):
                    continue
            return True
        return False
    if rule["kind"] == "dimensions":
        pattern = (
            r"(?<![\w.,+\-])("
            + NUMBER
            + r")\s*[x×]\s*("
            + NUMBER
            + r")\s*"
            + _unit_pattern(rule["unit"])
            + _QUANTITY_END
        )
        normalized = _matching_text(text)
        return any(
            all(
                Decimal(bounds[0]) <= _decimal(value) <= Decimal(bounds[1])
                for value, bounds in zip(m.groups(), rule["ranges"])
            )
            for m in re.finditer(pattern, normalized)
            if not _leading_operator(normalized, m.start())
        )
    return any(_phrase(text, alternative) for alternative in rule["alternatives"])
