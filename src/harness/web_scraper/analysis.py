"""Per-field diagnostics derived through the upstream matching functions."""

import json
from decimal import InvalidOperation
from functools import lru_cache

from harness.web_scraper.pipeline import PROFILE, digest, official_scorer

FIELDS = ("price", "sku", "availability")
REFERENCE_CONTROLS = (
    ("locale-thousands-price", "price", ["24990"], "24990", "24.99"),
    ("current-price-excludes-list-price", "price", ["99", "990"], "990.00", "1390"),
    (
        "product-id-not-request-token",
        "sku",
        ["140212369"],
        "140212369",
        "16067472565517510018016014",
    ),
    ("explicit-stock-not-default", "availability", ["OutOfStock"], "OutOfStock", "InStock"),
    ("default-stock-still-required", "availability", ["InStock"], "InStock", None),
    ("gtin-not-title-model", "sku", ["466/7999", "6935364088101"], "6935364088101", "TAPO C200"),
)


@lru_cache(maxsize=8)
def matchers(home, profile, source_sha256):
    return official_scorer(home, profile)


def field_results(home, reference, row, *, profile=PROFILE):
    source_sha = digest((home / "dataset/evaluate.py").read_bytes())
    scorer = matchers(home, profile, source_sha)
    prediction = (row.get("normalized_fields") or {}) if row.get("http_status") == 200 else {}
    result = {}
    for field in (*FIELDS, "OutOfStock"):
        expected = set(reference.get(field, []))
        actual = prediction.get(field)
        match = scorer["decimal_matching"] if field == "price" else scorer["hard_matching"]
        try:
            tp, fp, fn = match(expected, actual)
        except InvalidOperation:
            tp, fp, fn = scorer["hard_matching"](expected, actual)
        status = (
            "correct"
            if fp == fn == 0
            else ("missing" if actual is None else "unexpected" if not expected else "wrong")
        )
        result[field] = {
            "expected": sorted(expected),
            "actual": actual,
            "status": status,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }
    return result


def check_reference_controls(home, *, profile=PROFILE):
    """Paired positive/negative controls through the production field scorer."""
    for name, field, accepted, valid, invalid in REFERENCE_CONTROLS:
        for prediction, expected in ((valid, "correct"), (invalid, "incorrect")):
            result = field_results(
                home,
                {field: accepted},
                {"http_status": 200, "normalized_fields": {field: prediction}},
                profile=profile,
            )[field]
            if (result["status"] == "correct") != (expected == "correct"):
                raise ValueError("Reference correction regression: " + name)
    return [item[0] for item in REFERENCE_CONTROLS]


def native_error(row):
    if row.get("compatibility") == "structured_output":
        return ""
    if row.get("error"):
        return str(row["error"])
    payload = row.get("response") or {}
    if not isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False)
    value = (
        payload.get("error")
        or payload.get("message")
        or payload.get("errors")
        or payload.get("non_json_response")
    )
    if not value:
        return "No recognizable structured response"
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def failure_stage(row):
    if row.get("compatibility") == "structured_output":
        return "fields"
    error = native_error(row).lower()
    if row.get("http_status") is None:
        return "client_transport"
    if "context window" in error or "content is too large" in error:
        return "model_capacity"
    if "navigation timeout" in error:
        return "navigation"
    if "schema" in error:
        return "provider_schema"
    if "download page" in error or "third-party" in error or "fetching page" in error:
        return "provider_fetch"
    return "provider_response"
