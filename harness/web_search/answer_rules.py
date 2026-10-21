"""Versioned literal answer evidence rules derived from reference annotations.

These rules recognize supplied alternatives and numeric tolerances, not semantic
correctness, entailment, or inferred synonyms. A match can occur in unrelated text.
"""
import re
import unicodedata
from decimal import Decimal

RULE_VERSION = "reference-answer-evidence/1"
NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"

def _norm(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()

def _decimal(value):
    return Decimal(value.replace(",", ""))

# A matched quantity/unit cannot end before a rate, percentage or exponent.
# NFKC normalizes superscript unit powers (m² -> m2), covered by the word guard.
_QUANTITY_END = r"(?![\w%‰/^]|[.,]\d|\s*(?:[%‰/^]|per\b))"

def _phrase(text, phrase):
    # Keep numeric modifiers attached to their number, so 150 != 150%.
    pattern = NUMBER + r"(?:\s*[%‰])?|[^\W\d_]+|[♯♭]"
    def token(value):
        modifier = re.fullmatch(r"(" + NUMBER + r")\s*([%‰]?)", value)
        return str(_decimal(modifier[1]).normalize()) + modifier[2] if modifier else value
    normalized = _norm(text)
    matches = list(re.finditer(pattern, normalized))
    actual = [token(m.group()) for m in matches]
    wanted = [token(m.group()) for m in re.finditer(pattern, _norm(phrase))]
    if not wanted:
        return False
    for i in range(len(actual) - len(wanted) + 1):
        if actual[i:i+len(wanted)] != wanted:
            continue
        # Numeric references additionally retain complete quantities and units.
        if any(re.fullmatch(NUMBER + r"(?:\s*[%‰])?", m.group()) for m in re.finditer(pattern, _norm(phrase))):
            end = matches[i+len(wanted)-1].end()
            if not re.match(_QUANTITY_END, normalized[end:]):
                continue
        return True
    return False

def reference_rule(reference):
    """Return a JSON-serializable derivation; unknown annotations are explicit."""
    value = reference.strip()
    rule = {"version": RULE_VERSION, "reference": reference, "kind": "literal", "alternatives": [value], "unsupported_annotation": None}
    annotation = re.fullmatch(r"(.+?)\s*\(acceptable range:\s*(.+)\)", value, re.I)
    if annotation:
        base, body = annotation.groups()
        rule["alternatives"] = [base.strip()]
        # Preserve a fixed prefix for compound values such as years + days.
        span = re.fullmatch(r"(.*?)anything between\s+("+NUMBER+r")\s*(.*?)\s+and\s+("+NUMBER+r")\s*(.*)", body, re.I)
        if span:
            prefix, low, low_unit, high, high_unit = span.groups()
            # A repeated scale may occur before a final currency (million USD).
            if low_unit and not _norm(high_unit).startswith(_norm(low_unit)):
                rule["unsupported_annotation"] = body
                return rule
            if _decimal(low) > _decimal(high):
                rule["unsupported_annotation"] = body
                return rule
            rule.update(kind="numeric_range", lower=str(_decimal(low)), upper=str(_decimal(high)), unit=high_unit.strip(), prefix=prefix.strip())
            return rule
        dimension = re.fullmatch(r"\[("+NUMBER+r")-("+NUMBER+r")\]\s*[x×]\s*\[("+NUMBER+r")-("+NUMBER+r")\]\s*(.+)", body)
        if dimension:
            a,b,c,d,unit = dimension.groups()
            rule.update(kind="dimensions", ranges=[[str(_decimal(a)),str(_decimal(b))],[str(_decimal(c)),str(_decimal(d))]], unit=unit.strip())
            return rule
        rule["unsupported_annotation"] = body
        return rule
    also = re.fullmatch(r'(.+?)\s*\(also know[n]? as\s+(.+)\)', value, re.I)
    if also:
        base, alternatives = also.groups()
        aliases = [v.strip().strip('"') for v in re.split(r'\s+or\s+', alternatives)]
        rule.update(kind="explicit_alternatives", alternatives=[base.strip()]+aliases)
        return rule
    terminal = re.fullmatch(r"(.+?)\s*\(or\s+([^()]+)\)", value, re.I)
    if terminal:
        rule.update(kind="explicit_alternatives", alternatives=[v.strip() for v in terminal.groups()])
        return rule
    inline = re.search(r"([^\s,]+)\s*\(or\s+([^()]+)\)", value, re.I)
    if inline:
        first, second = inline.groups()
        rule.update(kind="explicit_alternatives", alternatives=[value[:inline.start()]+v+value[inline.end():] for v in (first,second)])
        return rule
    if "(" in value or ")" in value:
        rule["unsupported_annotation"] = "Uninterpreted parentheses retained literally"
    return rule

def _unit_pattern(unit):
    return r"\s*".join(re.escape(p) for p in _norm(unit).split())

def _numeric_matches(text, unit):
    # A number must be complete: 150 must not be found inside 1,150 or 150.9.
    suffix = r"\s*" + _unit_pattern(unit) if unit else ""
    pattern = r"(?<![\w.,+\-])("+NUMBER+r")" + suffix + _QUANTITY_END
    return re.finditer(pattern, _norm(text))

def match_reference_answer(reference, text):
    """Whether text contains explicitly allowed reference evidence (not truth)."""
    rule = reference_rule(reference)
    if rule["kind"] == "numeric_range":
        low, high = Decimal(rule["lower"]), Decimal(rule["upper"])
        normalized = _norm(text)
        for match in _numeric_matches(text, rule["unit"]):
            if not low <= _decimal(match.group(1)) <= high:
                continue
            if rule["prefix"]:
                prefix = _norm(rule["prefix"]).rstrip(", ")
                before = normalized[:match.start()].rstrip(", ")
                if not re.search(r"(?<!\w)" + re.escape(prefix) + r"$", before):
                    continue
            return True
        return False
    if rule["kind"] == "dimensions":
        pattern = r"(?<![\w.,+\-])("+NUMBER+r")\s*[x×]\s*("+NUMBER+r")\s*"+_unit_pattern(rule["unit"])+_QUANTITY_END
        return any(all(Decimal(bounds[0]) <= _decimal(value) <= Decimal(bounds[1]) for value,bounds in zip(m.groups(),rule["ranges"])) for m in re.finditer(pattern,_norm(text)))
    return any(_phrase(text, alternative) for alternative in rule["alternatives"])
