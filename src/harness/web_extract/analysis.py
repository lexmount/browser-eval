"""Per-field diagnostics derived through the upstream matching functions."""

import json
import math
import statistics
from collections import Counter
from decimal import InvalidOperation
from functools import lru_cache
from pathlib import Path

from harness.web_extract.pipeline import PROFILE, SCHEMA, digest, official_scorer

FIELDS = tuple(SCHEMA["properties"])


def score_model_comparison(plan_path, run, contract_path, home, *, stage):
    """Score an exact declared subset without new calls or reference leakage."""
    from jsonschema import Draft202012Validator

    from harness.record_io import canonical_json_sha256, file_sha256, read_json
    from harness.web_extract.model_comparison import request_body
    from harness.web_extract.pipeline import normalize

    plan_path, run, contract_path = Path(plan_path), Path(run), Path(contract_path)
    plan, contract = read_json(plan_path), read_json(contract_path)
    if plan["case_contract_sha256"] != file_sha256(contract_path):
        raise ValueError("Model case contract changed")
    binding = read_json(run / "binding.json")
    if binding["plan_sha256"] != file_sha256(plan_path):
        raise ValueError("Model plan differs from its collected evidence")
    index = read_json(run / "call-hashes.json")
    selected = {c["id"] for dataset in contract["stages"].values() for c in dataset[stage]}
    cases = {c["id"]: c for c in plan["cases"] if c["id"] in selected}
    if not cases or set(cases) != selected:
        raise ValueError("Model stage omits declared cases")
    originals = {c["id"]: c for dataset in contract["stages"].values() for c in dataset[stage]}
    if cases != originals:
        raise ValueError("Model plan changed the authoritative reference or input")
    models = {m["id"]: m for m in plan["models"]}
    rows = []
    for call in (c for c in plan["calls"] if c["phase"] == "comparison" and c["case_id"] in cases):
        key = canonical_json_sha256(call)
        path = run / "calls" / (key + ".json")
        if not path.exists() or index.get(key) != file_sha256(path):
            raise ValueError("Missing or changed model call evidence")
        record = read_json(path)
        case, model = cases[call["case_id"]], models[call["model_id"]]
        expected = {
            **binding,
            "call": call,
            "request_sha256": canonical_json_sha256(request_body(plan, model, case)),
        }
        if (
            record["binding"] != expected
            or canonical_json_sha256(record["request"]) != expected["request_sha256"]
        ):
            raise ValueError("Model response does not bind the declared request")
        response = record["response"]
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        valid = bool(
            response.get("success")
            and not list(Draft202012Validator(case["schema"]).iter_errors(data))
        )
        reference = case["reference"]
        native = {"http_status": 200 if response.get("success") else 0}
        if case["dataset"] == "swde":
            policy = contract["bindings"]["swde"]["reference_policy"]
            unavailable = {tuple(group) for group in policy["unavailable_reference_groups"]}
            native["normalized_fields"] = data
            fields = record_field_results(
                reference,
                native,
                text_matching=policy["text_matching"],
                unavailable_fields=[
                    field
                    for field in reference["values"]
                    if (reference["domain"], reference["site"], field) in unavailable
                ],
            )
            complete = all(v["status"] != "unavailable" for v in fields.values())
        else:
            native["normalized_fields"] = normalize(
                "crawl4ai-json", {"success": response.get("success", False), "data": data}
            )
            fields = field_results(Path(home), reference, native)
            excluded = set(case.get("reference_excluded_fields", []))
            fields = {k: v for k, v in fields.items() if k not in excluded and k != "OutOfStock"}
            complete = not (set(case["schema"]["properties"]) & excluded)
        counts = {
            k: sum(v[k] for v in fields.values() if v["status"] != "unavailable")
            for k in ("tp", "fp", "fn")
        }
        rows.append(
            {
                "model_id": call["model_id"],
                "case_id": case["id"],
                "dataset": case["dataset"],
                "schema_valid": valid,
                "fields": fields,
                "reference_complete": complete,
                "page_exact": (valid and all(v["status"] == "correct" for v in fields.values()))
                if complete
                else None,
                "timings": {
                    "first_any_output_seconds": response.get("first_any_output_seconds"),
                    "first_answer_seconds": response.get("first_answer_seconds"),
                    "elapsed_seconds": response.get(
                        "elapsed_seconds", response.get("complete_json_seconds")
                    ),
                    "complete_json_seconds": response.get("complete_json_seconds")
                    if response.get("success")
                    else None,
                },
                "call_sha256": index[key],
                **counts,
            }
        )
    expected = {(m, c) for m in models for c in cases}
    if len(rows) != len(expected) or {(r["model_id"], r["case_id"]) for r in rows} != expected:
        raise ValueError("Incomplete or duplicate model comparison population")

    def summarize(values):
        totals = Counter({k: sum(r[k] for r in values) for k in ("tp", "fp", "fn")})
        tp, fp, fn = (totals[k] for k in ("tp", "fp", "fn"))
        timers = {}
        for name in values[0]["timings"]:
            durations = sorted(r["timings"][name] for r in values if r["timings"][name] is not None)
            timers[name] = {
                "count": len(durations),
                "missing": len(values) - len(durations),
                "mean": statistics.mean(durations) if durations else None,
                "p50": statistics.median(durations) if durations else None,
                "p95": durations[math.ceil(0.95 * len(durations)) - 1] if durations else None,
            }
        return {
            "cases": len(values),
            "schema_valid": sum(r["schema_valid"] for r in values),
            "page_exact": sum(r["page_exact"] is True for r in values),
            "page_exact_denominator": sum(r["reference_complete"] for r in values),
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "timings": timers,
            **totals,
        }

    groups = {}
    for model in models:
        values = [r for r in rows if r["model_id"] == model]
        groups[model] = {
            **summarize(values),
            "datasets": {
                ds: summarize([r for r in values if r["dataset"] == ds])
                for ds in sorted({r["dataset"] for r in values})
            },
        }
    return {
        "stage": stage,
        "plan_sha256": binding["plan_sha256"],
        "case_contract_sha256": file_sha256(contract_path),
        "scoring_sha256": file_sha256(Path(__file__)),
        "groups": groups,
        "case_ids": sorted(cases),
        "details": rows,
        "review_scope": "automatic scoring only",
    }


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
    from harness.web_extract.pipeline import normalize

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
    from harness.web_extract.swde import canonical_text

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
                field in prediction
                if not isinstance(actual, list)
                else sum(not isinstance(value, str) for value in actual)
            ),
            "fn": len(expected - valid),
        }
    return result


def record_statistics(truth, rows, *, certified, scorer, reference_policy=None):
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
    policy = reference_policy or {"text_matching": "literal", "unavailable_reference_groups": []}
    unavailable = {tuple(group) for group in policy["unavailable_reference_groups"]}
    retry_seconds, retry_missing, physical_calls = 0.0, 0, 0
    for row in rows:
        reference = truth[row["case_id"]]
        fields = record_field_results(
            reference,
            row,
            text_matching=policy["text_matching"],
            unavailable_fields=[
                field
                for field in reference["values"]
                if (reference["domain"], reference["site"], field) in unavailable
            ],
        )
        field_counts.update(value["status"] for value in fields.values())
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
        "scores": scorer(truth, predictions, **(reference_policy or {})) if complete else None,
        "latency": latency,
        "field_outcomes": dict(field_counts),
        "failure_stages": dict(failures),
        "physical_calls": physical_calls,
        "retry_seconds": retry_seconds,
        "retry_missing_timers": retry_missing,
    }


def completion_metrics(rows, fields_by_case, *, records):
    """Add delivery-aware diagnostics without changing upstream field judgments.

    Whole-page accuracy excludes a page if any requested field lacks reference
    coverage. Empty-field accuracy retains every covered negative field, including
    failed calls in its denominator. Missing values are valid for native product
    APIs; array-schema tasks require an explicitly delivered empty array.
    """
    from decimal import Decimal, InvalidOperation

    from harness.web_extract.pipeline import normalize

    by_case = {row["case_id"]: row for row in rows}
    if len(by_case) != len(rows) or set(by_case) != set(fields_by_case):
        raise ValueError("Completion metrics require the complete unique case population")
    page_correct = page_total = empty_correct = empty_total = 0
    cases = {}
    for cid, fields in fields_by_case.items():
        row = by_case[cid]
        prediction = row.get("normalized_fields")
        if not records and "response" in row:
            # Inspect native types before product projection drops malformed values.
            prediction = normalize(
                row["method"],
                row["response"],
                schema={"type": "object"},
                preserve_native_types=True,
            )
        delivered = (
            row.get("http_status") == 200
            and row.get("compatibility") == "structured_output"
            and isinstance(prediction, dict)
        )
        prediction = prediction if isinstance(prediction, dict) else {}
        covered = {f: value for f, value in fields.items() if value["status"] != "unavailable"}
        eligible = bool(fields) and len(covered) == len(fields)

        def valid(field, current=prediction):
            value = current.get(field)
            if records:
                return (
                    field in current
                    and isinstance(value, list)
                    and all(isinstance(item, str) for item in value)
                )
            if value is None:
                return True
            if isinstance(value, (bool, dict, list)):
                return False
            definition = SCHEMA["properties"].get(field, {})
            if "number" in definition.get("type", []):
                try:
                    return Decimal(str(value)).is_finite()
                except (InvalidOperation, ValueError):
                    return False
            if "enum" in definition:
                return value in definition["enum"]
            return isinstance(value, (str, int, float))

        correct = {
            f: delivered
            and valid(f)
            and (value["exact"] if records else value["status"] == "correct")
            for f, value in covered.items()
        }
        full = (
            eligible and all(correct.values()) and (not records or set(prediction) == set(fields))
        )
        negatives = [f for f, value in covered.items() if not value["expected"]]
        negative_correct = sum(bool(correct[f]) for f in negatives)
        page_total += eligible
        page_correct += full
        empty_total += len(negatives)
        empty_correct += negative_correct
        cases[cid] = {
            "eligible": eligible,
            "delivered": delivered,
            "page_exact": bool(full) if eligible else None,
            "empty_fields": len(negatives),
            "empty_fields_correct": negative_correct,
        }

    def rate(correct, total):
        return {"correct": correct, "total": total, "rate": correct / total if total else None}

    return {
        "page_exact": rate(page_correct, page_total),
        "empty_fields": rate(empty_correct, empty_total),
        "planned_pages": len(fields_by_case),
        "excluded_pages": len(fields_by_case) - page_total,
        "cases": cases,
    }


def check_record_audit(home, truth, rows, audit, *, reference_revision=None):
    """Recompute a complete audit from current native answers and source authority."""
    from pathlib import Path

    from harness.web_extract.dataset import benchmark_config
    from harness.web_extract.pipeline import execution_identity, normalize

    adapter = benchmark_config(home)["adapter"]
    expected = {(row["method"], row["case_id"]) for row in rows}
    methods = sorted({method for method, _ in expected})
    if (
        not methods
        or len(expected) != len(rows)
        or expected != {(method, cid) for method in methods for cid in truth}
        or audit.get("schema") != "record-complete-audit/1"
        or audit.get("review_scope") != "all"
        or audit.get("review_state") != "complete"
        or audit.get("unresolved") != 0
        or audit.get("unresolved_fields") != []
        or audit.get("accepted_unreviewed_passes") != 0
        or audit.get("case_count") != len(truth)
        or audit.get("result_count") != len(expected)
        or audit.get("original_results_reviewed") != len(expected)
        or set(audit.get("results", {})) != {method + ":" + cid for method, cid in expected}
        or not audit.get("review_receipts")
        or any(
            not isinstance(r.get("reviewer", {}).get("model"), str)
            or not r["reviewer"]["model"].strip()
            for r in audit.get("review_receipts", [])
        )
        or set(audit.get("sources", {})) != set(truth)
        or any(
            set(audit["sources"][cid].get(section, {})) != set(truth[cid]["values"])
            for cid in truth
            for section in ("source_fields", "reference_review")
        )
        or audit.get("regression_ids") != adapter.check_reference_controls()
    ):
        raise ValueError("Required complete record review is missing or unresolved")
    for key, path in (
        ("official_reference_sha256", home / "dataset/dataset/ground-truth.json"),
        ("dataset_manifest_sha256", home / "dataset/manifest.json"),
        ("schemas_sha256", home / "dataset/schemas.json"),
        ("scorer_sha256", home / "dataset/evaluate.py"),
        ("adapter_sha256", Path(adapter.__file__)),
    ):
        if audit.get(key) != digest(path.read_bytes()):
            raise ValueError("Record audit source, schema or scorer binding changed")
    if audit.get("source_hashes") != {
        cid: digest((home / "dataset/html" / (cid + ".html")).read_bytes()) for cid in truth
    }:
        raise ValueError("Record audit frozen source population changed")
    references = truth
    policy = {"unavailable_reference_groups": [], "text_matching": "literal"}
    authority_sha = None
    decisions = {}
    if reference_revision is not None:
        authority_sha = digest(Path(reference_revision).read_bytes())
        references, policy = adapter.load_reference_revision(home, reference_revision)
        revision = json.loads(Path(reference_revision).read_bytes())
        decisions = {(d["case_id"], d["field"]): d for d in revision.get("field_decisions", [])}
    if (
        audit.get("reference_revision_sha256") != authority_sha
        or audit.get("audited_reference") != references
        or audit.get("reference_policy") != policy
    ):
        raise ValueError("Record audit reference authority differs from current scoring inputs")
    for cid, source in audit["sources"].items():
        expected_decisions = {
            field: decisions[(cid, field)]
            for field in truth[cid]["values"]
            if (cid, field) in decisions
        }
        if source.get("source_decisions", {}) != expected_decisions:
            raise ValueError("Record audit final source decisions differ from their authority")
    unavailable = {tuple(group) for group in policy["unavailable_reference_groups"]}
    predictions = {method: {} for method in methods}
    for row in rows:
        method, cid = row["method"], row["case_id"]
        reviewed = audit["results"][method + ":" + cid]
        current = normalize(
            method, row.get("response", {}), schema=adapter.task_schema(home, truth[cid])
        )
        if current != row.get("normalized_fields"):
            raise ValueError("Record projection correction requires its declared source assessment")
        fields = record_field_results(
            references[cid],
            {**row, "normalized_fields": current},
            text_matching=policy["text_matching"],
            unavailable_fields=[
                field
                for field in references[cid]["values"]
                if (truth[cid]["domain"], truth[cid]["site"], field) in unavailable
            ],
        )
        if (
            reviewed.get("execution_sha256") != execution_identity(row)
            or reviewed.get("native_response_sha256")
            != digest(json.dumps(row.get("response"), sort_keys=True, ensure_ascii=False).encode())
            or reviewed.get("audited_fields") != fields
        ):
            raise ValueError(
                "Record audit differs from current native execution or field judgments"
            )
        predictions[method][cid] = (current or {}) if row.get("http_status") == 200 else {}
    scores = {
        method: adapter.score(references, predictions[method], **policy) for method in methods
    }
    if audit.get("audited_scores") != scores:
        raise ValueError("Record audit aggregates differ from current native answers")
