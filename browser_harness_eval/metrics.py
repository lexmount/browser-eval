from __future__ import annotations

import re
import statistics
from collections import defaultdict
from typing import Any


METRIC_DEFINITIONS = {
    "transport_success": "Provider completed the request and classified it as successful.",
    "content_pass": "At least one pre-registered content marker was present in the returned content.",
    "marker_recall": "Matched markers divided by all markers registered for the case.",
    "elapsed_ms": "Client wall-clock duration, including network and provider processing.",
    "deadline_overrun": "Elapsed wall-clock time exceeded the configured timeout value; socket timeouts are not assumed to be hard deadlines.",
    "content_chars": "Unicode character count of the persisted provider output.",
    "markdown_headings": "Number of Markdown heading lines in the output.",
    "markdown_links": "Number of Markdown links with HTTP(S) targets.",
    "html_tags": "Number of HTML-like tags remaining in the output.",
}


def evaluate_content(content: str, expected: list[str]) -> dict[str, Any]:
    folded = content.casefold()
    matched = [marker for marker in expected if marker.casefold() in folded]
    return {
        "content_pass": bool(matched),
        "matched_markers": matched,
        "expected_markers": expected,
        "marker_hits": len(matched),
        "marker_total": len(expected),
        "marker_recall": round(len(matched) / len(expected), 4) if expected else 1.0,
        "content_chars": len(content),
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
