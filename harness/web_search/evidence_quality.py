"""Deterministic sensitivity checks for published factual retrieval datasets.

Raw results and frozen metrics remain immutable. Exclusions preserve rank slots.
"""
import re

from .core import factual_evidence

CONTAMINATION_RULE = "simpleqa-name-in-url-or-title/1"


def is_benchmark_page(result):
    """Recognize explicit SimpleQA mirrors, not every dataset-hosted document."""
    text = result.get("url", "") + " " + result.get("title", "")
    return bool(re.search(r"simple[\s_\-]*qa", text, re.IGNORECASE))


def factual_sensitivity(case, results):
    retained = []
    excluded = []
    for rank, result in enumerate(results[:5], 1):
        if is_benchmark_page(result):
            excluded.append(rank)
            retained.append({"url": f"https://benchmark-excluded.invalid/{rank}"})
        else:
            retained.append(result)
    return {
        "rule": CONTAMINATION_RULE,
        "excluded_ranks": excluded,
        "metrics": factual_evidence(case, retained),
    }
