"""Deterministic sensitivity checks for published factual retrieval datasets.

Raw results and frozen metrics remain immutable. Exclusions preserve rank slots.
"""
import re
import unicodedata

from .core import factual_evidence

LEGACY_CONTAMINATION_RULE = "simpleqa-name-in-url-or-title/1"
CONTAMINATION_RULE = "benchmark-name-or-question-with-evaluation-fields/2"


def is_benchmark_page(result, *, case=None, rule=CONTAMINATION_RULE):
    """Exclude named mirrors or the exact question accompanied by evaluation fields."""
    if rule not in (LEGACY_CONTAMINATION_RULE, CONTAMINATION_RULE):
        raise ValueError("Unsupported factual contamination rule")
    identity = result.get("url", "") + " " + result.get("title", "")
    if re.search(r"simple[\s_\-]*qa", identity, re.IGNORECASE):
        return True
    if rule == LEGACY_CONTAMINATION_RULE or not case:
        return False
    text = " ".join(str(result.get(key) or "") for key in ("title", "snippet", "summary", "content"))
    def compact(value):
        return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())
    question = compact(case.get("query", ""))
    if not question or question not in compact(text):
        return False
    return bool(re.search(
        r"\b(?:ground[\s_\-]*truth|reference[\s_\-]*answers?|gold[\s_\-]*(?:answers?|labels?)|model[\s_\-]*(?:answers?|responses?)|grader[\s_\-]*(?:responses?|results?)|is[\s_\-]*correct)\b",
        text, re.IGNORECASE))


def factual_sensitivity(case, results, *, rule=CONTAMINATION_RULE):
    retained = []
    excluded = []
    for rank, result in enumerate(results[:5], 1):
        if is_benchmark_page(result, case=case, rule=rule):
            excluded.append(rank)
            retained.append({"url": f"https://benchmark-excluded.invalid/{rank}"})
        else:
            retained.append(result)
    return {
        "rule": rule,
        "excluded_ranks": excluded,
        "metrics": factual_evidence(case, retained),
    }
