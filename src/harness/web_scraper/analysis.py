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
    if "values" in reference:
        return record_field_results(reference, row)
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


def product_audit_scores(truth, reviews, methods, excluded, scorer):
    result = {}
    for method in methods:
        result[method] = {}
        for field in (*FIELDS, "OutOfStock"):
            values = [
                reviews[method + ":" + cid]["audited_fields"][field]
                for cid in truth
                if field not in excluded.get(cid, [])
                and not (field == "OutOfStock" and "availability" in excluded.get(cid, []))
            ]
            result[method][field] = {
                **(
                    scorer["metrics_from_tp_fp_fns"]([(v["tp"], v["fp"], v["fn"]) for v in values])
                    if values
                    else {"precision": None, "recall": None, "f1": None}
                ),
                "cases": len(values),
            }
    return result


def check_product_audit(home, truth, rows, audit, *, profile):
    """Reject cached judgments whose native projection, reference or scorer changed."""
    from harness.web_scraper.pipeline import normalize

    if (
        audit.get("official_reference_sha256")
        != digest((home / "dataset/dataset/ground-truth.json").read_bytes())
        or audit.get("scorer_sha256") != digest((home / "dataset/evaluate.py").read_bytes())
        or set(audit.get("audited_reference", {})) != set(truth)
    ):
        raise ValueError("Audit reference or scorer binding differs from current scoring inputs")
    excluded = audit.get("excluded_fields", {})
    if set(excluded) - set(truth) or any(set(fields) - set(FIELDS) for fields in excluded.values()):
        raise ValueError("Audit exclusion is outside the declared reference scope")
    recomputed = {}
    for row in rows:
        key = row["method"] + ":" + row["case_id"]
        current = {**row, "normalized_fields": normalize(row["method"], row.get("response", {}))}
        fields = field_results(
            home, audit["audited_reference"][row["case_id"]], current, profile=profile
        )
        if fields != audit["results"][key].get("audited_fields"):
            raise ValueError("Cached audit field judgments differ from current native projection")
        recomputed[key] = {"audited_fields": fields}
    expected = product_audit_scores(
        truth,
        recomputed,
        sorted({r["method"] for r in rows}),
        excluded,
        official_scorer(home, profile),
    )
    if audit.get("audited_scores") != expected:
        raise ValueError("Cached audit aggregates differ from current field judgments")


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
        return ""
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


def record_field_results(reference, row, *, text_matching="literal", unavailable_fields=()):
    """Expose exact-set and official page-hit outcomes without changing saved scores."""
    from harness.web_scraper.swde import canonical_text

    if text_matching not in ("literal", "whitespace"):
        raise ValueError("Unsupported record text matching policy")
    unavailable = set(unavailable_fields)
    if unavailable - set(reference["values"]) or any(
        reference["values"][field] for field in unavailable
    ):
        raise ValueError("Unavailable record fields must be declared and unannotated")
    prediction = (row.get("normalized_fields") or {}) if row.get("http_status") == 200 else {}
    result = {}
    for field, gold in reference["values"].items():
        actual = prediction.get(field, [])
        valid = (
            {
                canonical_text(value) if text_matching == "whitespace" else value
                for value in actual
                if isinstance(value, str)
            }
            if isinstance(actual, list)
            else set()
        )
        expected = {
            canonical_text(value) if text_matching == "whitespace" else value for value in gold
        }
        if field in unavailable:
            result[field] = {
                "expected": [],
                "actual": actual,
                "status": "unavailable",
                "hit": None,
                "exact": None,
                "tp": None,
                "fp": None,
                "fn": None,
            }
            continue
        invalid = not isinstance(actual, list) or any(
            not isinstance(value, str) for value in actual
        )
        exact = not invalid and expected == valid and len(valid) == len(actual)
        if exact:
            status = "correct"
        elif invalid:
            status = "invalid"
        elif not valid:
            status = "missing"
        elif not expected:
            status = "unexpected"
        elif expected & valid:
            status = "partial"
        else:
            status = "wrong"
        result[field] = {
            "expected": sorted(expected),
            "actual": actual,
            "status": status,
            "hit": bool(expected & valid),
            "exact": exact,
            "tp": len(expected & valid),
            "fp": len(valid - expected)
            + (
                bool(actual)
                if not isinstance(actual, list)
                else sum(not isinstance(value, str) for value in actual)
            ),
            "fn": len(expected - valid),
        }
    return result


def record_statistics(truth, rows, *, certified, scorer):
    """Summarize one declared method without scoring an incomplete population."""
    import math
    import statistics
    from collections import Counter

    by_case = {row["case_id"]: row for row in rows}
    if len(by_case) != len(rows) or set(by_case) - set(truth):
        raise ValueError("Record report requires one selected result per declared case")
    complete = certified and set(by_case) == set(truth)
    durations = sorted(
        row["elapsed_seconds"] for row in rows if row.get("elapsed_seconds") is not None
    )
    latency = {
        "timed": len(durations),
        "missing": len(rows) - len(durations),
        "mean": statistics.mean(durations) if durations else None,
        "p50": statistics.median(durations) if durations else None,
        "p95": durations[max(0, math.ceil(len(durations) * 0.95) - 1)] if durations else None,
    }
    field_counts, failures = Counter(), Counter()
    exact_pages = 0
    retry_seconds, retry_missing, physical_calls = 0.0, 0, 0
    for row in rows:
        fields = record_field_results(truth[row["case_id"]], row)
        field_counts.update(value["status"] for value in fields.values())
        exact_pages += bool(fields) and all(value["exact"] for value in fields.values())
        if row.get("compatibility") != "structured_output":
            failures[failure_stage(row)] += 1
        attempts = row.get("physical_attempts", [row])
        physical_calls += len(attempts)
        if "retry_execution_seconds" in row:
            retry_seconds += row["retry_execution_seconds"]
            retry_missing += row.get("retry_missing_timers", 0)
        else:
            retry_seconds += sum(
                value["elapsed_seconds"]
                for value in attempts[:-1]
                if value.get("elapsed_seconds") is not None
            )
            retry_missing += sum(value.get("elapsed_seconds") is None for value in attempts[:-1])
    predictions = (
        {
            case_id: (
                (by_case[case_id].get("normalized_fields") or {})
                if by_case[case_id].get("http_status") == 200
                else {}
            )
            for case_id in truth
        }
        if complete
        else None
    )
    return {
        "planned": len(truth),
        "executed": len(rows),
        "unexecuted": len(truth) - len(rows),
        "certified": complete,
        "structured": sum(row.get("compatibility") == "structured_output" for row in rows),
        "schema_invalid": sum(row.get("schema_valid") is False for row in rows),
        "scores": scorer(truth, predictions) if complete else None,
        "latency": latency,
        "field_outcomes": dict(field_counts),
        "failure_stages": dict(failures),
        "exact_pages": exact_pages,
        "physical_calls": physical_calls,
        "retry_seconds": retry_seconds,
        "retry_missing_timers": retry_missing,
    }
