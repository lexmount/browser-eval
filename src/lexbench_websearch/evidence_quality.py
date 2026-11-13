"""Deterministic sensitivity checks for published factual retrieval datasets.

Raw results and frozen metrics remain immutable. Exclusions preserve rank slots.
"""

import re
import unicodedata
import json
from functools import cache

from .scoring import factual_evidence

LEGACY_CONTAMINATION_RULE = "simpleqa-name-in-url-or-title/1"
EVALUATION_FIELDS_RULE = "benchmark-name-or-question-with-evaluation-fields/2"
CONTAMINATION_RULE = "question-with-evaluation-or-dataset-viewer-structure/3"
DATASET_ROWS_RULE = "question-with-structured-dataset-rows/4"


@cache
def default_patterns():
    from .inputs import definition_path

    return json.loads(definition_path("contamination-patterns.json").read_bytes())


def is_benchmark_page(result, *, case=None, rule=None):
    """Exclude named mirrors or the exact question accompanied by evaluation fields."""
    rule = rule or (case or {}).get('factual_contamination_rule', CONTAMINATION_RULE)
    if rule not in (
        LEGACY_CONTAMINATION_RULE,
        EVALUATION_FIELDS_RULE,
        CONTAMINATION_RULE,
        DATASET_ROWS_RULE,
    ):
        raise ValueError("Unsupported factual contamination rule")
    identity = result.get("url", "") + " " + result.get("title", "")
    patterns = (case["contamination_patterns"]
                if case is not None and "contamination_patterns" in case else default_patterns())
    if any(
        re.search(pattern, identity, re.IGNORECASE)
        for pattern in patterns["identity_patterns"]
    ):
        return True
    if rule == LEGACY_CONTAMINATION_RULE or not case:
        return False
    text = " ".join(
        str(result.get(key) or "") for key in ("title", "snippet", "summary", "content")
    )
    if rule == DATASET_ROWS_RULE and (
        re.search(r'dataset\s+viewer|previewmetadata|sql\s+console', text, re.I)
        and re.search(r'question\s+string', text, re.I)
        and re.search(r'answer\s+string', text, re.I)
        or all(re.search(r'"' + key + r'"\s*:', text, re.I)
               for key in ('answer_type', 'topic', 'urls'))
    ):
        return True

    def compact(value):
        return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())

    question = compact(case.get("query", ""))
    if not question or question not in compact(text):
        return False
    evaluation_fields = bool(
        re.search(
            r"\b(?:ground[\s_\-]*truth|reference[\s_\-]*answers?|gold[\s_\-]*(?:answers?|labels?)|model[\s_\-]*(?:answers?|responses?)|grader[\s_\-]*(?:responses?|results?)|is[\s_\-]*correct)\b",
            text,
            re.IGNORECASE,
        )
    )
    if rule == EVALUATION_FIELDS_RULE:
        return evaluation_fields
    dataset_viewer = bool(
        re.search(r"subsets\s+and\s+splits", text, re.I)
        and re.search(r"select\s+\*\s+from\s+[\"`]?train\b", text, re.I)
    )
    validation_log = bool(
        re.search(r"hidden[\s_]+log", text, re.I)
        and re.search(r"answer\s+validation\s+failed", text, re.I)
        and re.search(r"expected\s*:", text, re.I)
        and re.search(r"received\s*:", text, re.I)
    )
    named_benchmark = any(
        re.search(pattern, text, re.I) for pattern in patterns["body_patterns"]
    )
    structured_dataset = rule == DATASET_ROWS_RULE and bool(
        re.search(r'dataset\s+viewer|previewmetadata|sql\s+console|formats\s*:\s*parquet', text, re.I)
        and (re.search(r'question\s+string', text, re.I)
             and re.search(r'answer\s+string', text, re.I)
             or re.search(r'dataset\s+viewer', text, re.I))
    )
    return evaluation_fields or dataset_viewer or validation_log or named_benchmark or structured_dataset


def factual_sensitivity(case, results, *, rule=None, cutoff=5):
    rule = rule or case.get('factual_contamination_rule', CONTAMINATION_RULE)
    retained = []
    excluded = []
    for rank, result in enumerate(results[:cutoff], 1):
        if is_benchmark_page(result, case=case, rule=rule):
            excluded.append(rank)
            retained.append({"url": f"https://benchmark-excluded.invalid/{rank}"})
        else:
            retained.append(result)
    return {
        "rule": rule,
        "excluded_ranks": excluded,
        "metrics": factual_evidence(case, retained, cutoff=cutoff),
    }
