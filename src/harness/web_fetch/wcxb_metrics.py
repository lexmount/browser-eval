"""WCXB word and snippet metrics, bound as one module by ``wcxb-metric-bindings.json``.

WCXB metrics, Murrough Foley (2026), CC-BY-4.0. Adapted from evaluate.py at
c039d5ee9f5a3a984a0e167e63aacd04e76e78a9; this is provenance, not code loaded
from a dataset or an external checkout.
Source: https://github.com/Murrough-Foley/web-content-extraction-benchmark
License: https://creativecommons.org/licenses/by/4.0/

The binding covers the per-page metric functions and everything they call: this
module may import only the standard library, and a code change here needs a new
binding, while a docstring, comment or formatting change does not. It does not
cover wcxb_scoring.py, which chooses the arguments, classifies pages, applies
exclusions and aggregates the scores.
"""

from __future__ import annotations

import re
from collections import Counter


def word_f1(predicted: str, reference: str) -> tuple[float, float, float]:
    """Official lowercase Unicode-word multiset precision, recall and F1."""
    predicted_words = re.findall(r'\w+', (predicted or '').lower())
    reference_words = re.findall(r'\w+', (reference or '').lower())
    if not reference_words:
        return (1.0, 1.0, 1.0) if not predicted_words else (0.0, 0.0, 0.0)
    if not predicted_words:
        return 0.0, 0.0, 0.0
    overlap = sum((Counter(predicted_words) & Counter(reference_words)).values())
    precision, recall = overlap / len(predicted_words), overlap / len(reference_words)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def snippet_check(text: str, snippets: list[str]) -> float:
    """Official case-insensitive literal substring fraction; no word gate."""
    return sum(snippet.lower() in text.lower() for snippet in snippets) / len(snippets) if snippets else 1.0
