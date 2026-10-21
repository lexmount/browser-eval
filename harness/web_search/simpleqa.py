"""Build a deterministic Search-API profile from the frozen SimpleQA Verified CSV."""

import csv
import io
import random
import re
from copy import deepcopy


def _urls(value):
    urls = []
    for part in value.split(","):
        match = re.search(r"https?://\S+", part.strip())
        if not match:
            continue
        url = match.group(0)
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        if url not in urls:
            urls.append(url)
    return urls


def sample_cases(csv_bytes, *, size, seed):
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    if not 0 < size <= len(rows):
        raise ValueError("Sample size must be within the frozen dataset")
    selected = list(rows)
    random.Random(seed).shuffle(selected)
    origin = (
        "https://huggingface.co/datasets/google/simpleqa-verified/resolve/"
        "ed392714d14fa5225b07c2b456453983bc75f690/simpleqa_verified.csv"
    )
    return [
        {
            "id": "sqv-" + row["original_index"],
            "family": "web",
            "category": "factual",
            "language": "en",
            "query": row["problem"],
            "origin": origin,
            "provenance_kind": "public_labeled",
            "derivation": (
                f"Unchanged publisher question; deterministic random sample "
                f"of {size} from 1,000 rows with seed {seed}"
            ),
            "reference_answer": row["answer"],
            "reference_urls": _urls(row["urls"]),
            "topic": row["topic"],
            "answer_type": row["answer_type"],
            "multi_step": row["multi_step"].casefold() == "true",
            "requires_reasoning": row["requires_reasoning"].casefold() == "true",
        }
        for row in selected[:size]
    ]


def sampled_profile(template, csv_bytes, *, size=500, seed=20261021):
    profile = deepcopy(template)
    profile["title"] = f"SimpleQA Verified retrieval diagnostic — fixed {size}-query sample"
    profile["cases"] = sample_cases(csv_bytes, size=size, seed=seed)
    profile["methods"] = [
        method for method in profile["methods"]
        if method["id"] in {"lexmount", "deepseek", "bocha"}
    ]
    profile["protocol"].update({
        "preserve_publisher_queries": True,
        "sample": {
            "population": 1000,
            "size": size,
            "seed": seed,
            "algorithm": "Python random.Random(seed).shuffle; first N rows",
            "selection_before_calls": True,
        },
        "quality": (
            "Deterministic labeled-source hit@5 and answer-cue hit@5; no answer model "
            "and no model judge. This is a retrieval adaptation, not the official SimpleQA score."
        ),
        "comparison": (
            "All providers receive the same unchanged questions and request five core SERP "
            "results with optional content and summary fields disabled."
        ),
    })
    profile["cohort_revision"] = {
        "reason": (
            "Replace the length-filtered 831-query cohort with a preregistered fixed random "
            "sample from all 1,000 publisher rows; compare only the common core SERP mode."
        ),
        "sample_size": size,
        "sample_seed": seed,
        "source_revision": "ed392714d14fa5225b07c2b456453983bc75f690",
        "source_sha256": "fb990cca0fae8515ae2f51b794d5fa791d095c1f919ecdbe22d0c1642e9ab966",
    }
    return profile
