from __future__ import annotations

import re
import hashlib
import statistics
from collections import defaultdict
from typing import Any


METRIC_DEFINITIONS = {
    "transport_success": "Provider completed the request and classified it as successful.",
    "content_pass": "All deterministic gates pass: legacy marker, minimum length, required fact coverage, and no forbidden error-page marker.",
    "marker_recall": "Matched markers divided by all markers registered for the case.",
    "elapsed_ms": "Client wall-clock duration, including network and provider processing.",
    "deadline_overrun": "Elapsed wall-clock time exceeded the configured timeout value; socket timeouts are not assumed to be hard deadlines.",
    "content_chars": "Unicode character count of the persisted provider output.",
    "markdown_headings": "Number of Markdown heading lines in the output.",
    "markdown_links": "Number of Markdown links with HTTP(S) targets.",
    "html_tags": "Number of HTML-like tags remaining in the output.",
    "fact_coverage": "Matched pre-registered fact groups divided by all required fact groups; alternatives within a group are equivalent.",
    "forbidden_hits": "Pre-registered error-page, challenge or access-denied strings found in output.",
    "content_sha256": "SHA-256 of the exact persisted output, used to verify report previews against artifacts.",
    "unique_line_ratio": "Unique non-empty normalized lines divided by all non-empty lines; a diagnostic for duplicated boilerplate.",
}


def evaluate_content(
    content: str,
    expected: list[str],
    *,
    required_facts: list[dict[str, Any]] | None = None,
    forbidden_any: list[str] | None = None,
    min_content_chars: int = 1,
    fact_coverage_threshold: float = 1.0,
) -> dict[str, Any]:
    folded = content.casefold()
    matched = [marker for marker in expected if marker.casefold() in folded]
    facts = required_facts or []
    fact_results = []
    for index, fact in enumerate(facts):
        alternatives = [str(item) for item in fact.get("alternatives", [])]
        hits = [item for item in alternatives if item.casefold() in folded]
        fact_results.append({
            "id": str(fact.get("id") or f"fact-{index + 1}"),
            "alternatives": alternatives,
            "matched": hits,
            "pass": bool(hits),
        })
    fact_hits = sum(item["pass"] for item in fact_results)
    fact_coverage = round(fact_hits / len(fact_results), 4) if fact_results else 1.0
    forbidden = [item for item in (forbidden_any or []) if item.casefold() in folded]
    lines = [re.sub(r"\s+", " ", line).strip().casefold() for line in content.splitlines()]
    lines = [line for line in lines if line]
    unique_line_ratio = round(len(set(lines)) / len(lines), 4) if lines else 1.0
    legacy_pass = bool(matched) if expected else True
    length_pass = len(content) >= min_content_chars
    content_pass = legacy_pass and length_pass and fact_coverage >= fact_coverage_threshold and not forbidden
    return {
        "content_pass": content_pass,
        "legacy_marker_pass": legacy_pass,
        "matched_markers": matched,
        "expected_markers": expected,
        "marker_hits": len(matched),
        "marker_total": len(expected),
        "marker_recall": round(len(matched) / len(expected), 4) if expected else 1.0,
        "fact_results": fact_results,
        "fact_hits": fact_hits,
        "fact_total": len(fact_results),
        "fact_coverage": fact_coverage,
        "fact_coverage_threshold": fact_coverage_threshold,
        "forbidden_hits": forbidden,
        "minimum_content_chars": min_content_chars,
        "length_pass": length_pass,
        "content_chars": len(content),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "unique_line_ratio": unique_line_ratio,
        "markdown_headings": len(re.findall(r"(?m)^#{1,6}\s+", content)),
        "markdown_links": len(re.findall(r"\[[^]]+]\(https?://", content)),
        "html_tags": len(re.findall(r"<[^>]+>", content)),
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return round(ordered[index], 2)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_provider[record["provider"]].append(record)
        by_case[record["case_id"]].append(record)

    provider_summary = {}
    for provider, items in sorted(by_provider.items()):
        elapsed = [float(item["elapsed_ms"]) for item in items]
        successful_elapsed = [
            float(item["elapsed_ms"]) for item in items if item["ok"]
        ]
        provider_summary[provider] = {
            "provider_type": items[0]["provider_type"],
            "attempts": len(items),
            "transport_success_rate": round(sum(item["ok"] for item in items) / len(items), 4),
            "content_pass_rate": round(
                sum(item["metrics"]["content_pass"] for item in items) / len(items), 4
            ),
            "mean_marker_recall": round(
                statistics.mean(item["metrics"]["marker_recall"] for item in items), 4
            ),
            "mean_fact_coverage": round(
                statistics.mean(item["metrics"].get("fact_coverage", item["metrics"]["marker_recall"]) for item in items), 4
            ),
            "p50_ms": percentile(successful_elapsed, 0.50),
            "p95_ms": percentile(successful_elapsed, 0.95),
            "all_attempts_p95_ms": percentile(elapsed, 0.95),
            "median_content_chars": round(
                statistics.median(item["metrics"]["content_chars"] for item in items), 1
            ),
            "deadline_overrun_count": sum(
                bool(item["metrics"].get("deadline_overrun")) for item in items
            ),
            "failure_types": dict(sorted(_failure_counts(items).items())),
        }

    case_summary = {}
    for case_id, items in sorted(by_case.items()):
        case_summary[case_id] = {
            "name": items[0]["case_name"],
            "category": items[0]["category"],
            "attempts": len(items),
            "transport_success_rate": round(sum(item["ok"] for item in items) / len(items), 4),
            "content_pass_rate": round(
                sum(item["metrics"]["content_pass"] for item in items) / len(items), 4
            ),
        }
    return {"providers": provider_summary, "cases": case_summary}


def _failure_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        if not item["ok"]:
            counts[item.get("error_type") or "unknown_transport_failure"] += 1
        elif not item["metrics"]["content_pass"]:
            counts["content_validation_failure"] += 1
    return counts
