"""Rebuild the preregistered length-qualified SimpleQA retrieval population."""

import csv
import io
import re
from copy import deepcopy

from .core import digest

SOURCE_REVISION = "ed392714d14fa5225b07c2b456453983bc75f690"
SOURCE_SHA256 = "fb990cca0fae8515ae2f51b794d5fa791d095c1f919ecdbe22d0c1642e9ab966"
SOURCE_URL = f"https://huggingface.co/datasets/google/simpleqa-verified/resolve/{SOURCE_REVISION}/simpleqa_verified.csv"


def _urls(value):
    urls = []
    for part in value.split(","):
        match = re.match(r"https?://\S+", part.strip())
        if not match:
            continue
        url = match.group(0)
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        if url not in urls:
            urls.append(url)
    return urls


def eligible_cases(csv_bytes):
    """Select by the pre-call shared input limit, retaining original CSV order."""
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    if len({row['original_index'] for row in rows}) != len(rows):
        raise ValueError("Duplicate publisher question identity")
    return [{
        "id": "sqv-" + row["original_index"],
        "family": "web", "category": "factual", "language": "en",
        "query": row["problem"], "origin": SOURCE_URL,
        "provenance_kind": "public_labeled",
        "derivation": "Unchanged question; all publisher questions of at most 128 Unicode characters are included",
        "reference_answer": row["answer"], "reference_urls": _urls(row["urls"]),
        "topic": row["topic"], "answer_type": row["answer_type"],
    } for row in rows if len(row["problem"]) <= 128]


def rebuild_profile(template, csv_bytes):
    """Reconstruct the frozen 831 cases without changing its measured contract."""
    if digest(csv_bytes) != SOURCE_SHA256:
        raise ValueError("Publisher CSV SHA256 does not match the frozen source")
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    cases = eligible_cases(csv_bytes)
    if len(rows) != 1000 or len(cases) != 831:
        raise ValueError("Frozen population must contain 1,000 source and 831 eligible questions")
    if [m['id'] for m in template['methods']] != [
        'lexmount', 'deepseek', 'bocha', 'deepseek-content', 'bocha-summary'
    ]:
        raise ValueError("Template must retain the complete frozen five-method collection contract")
    if template.get('cases') != cases:
        raise ValueError("Template population differs from the frozen CSV length qualification")
    if template.get('reference_artifacts', {}).get(SOURCE_URL, {}).get('sha256') != SOURCE_SHA256:
        raise ValueError("Template does not bind the frozen source CSV")
    profile = deepcopy(template)
    profile["cases"] = cases
    return profile
