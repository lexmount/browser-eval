from __future__ import annotations

import re
import hashlib
import statistics
import unicodedata
from collections import Counter, defaultdict
from typing import Any


METRIC_DEFINITIONS = {
    "transport_success": "供应商完成请求，并将响应判为请求成功；它不代表正文正确。",
    "content_pass": "机器内容初判：显著标记、最小长度、全部事实组和反向错误标记四道确定性规则均通过。",
    "marker_recall": "命中的显著标记数除以该案例登记的显著标记总数。",
    "elapsed_ms": "客户端墙钟耗时，包含网络与供应商处理时间。",
    "deadline_overrun": "实际墙钟耗时超过配置的 timeout；socket timeout 不被假定为严格截止时间。",
    "content_chars": "持久化供应商输出的 Unicode 字符数。",
    "markdown_headings": "输出中的 Markdown 标题行数量。",
    "markdown_links": "输出中目标为 HTTP(S) 的 Markdown 链接数量。",
    "html_tags": "输出中残留的类 HTML 标签数量。",
    "fact_coverage": "命中的预登记事实组除以全部必需事实组；同组可接受表达等价。",
    "forbidden_hits": "输出中出现的预登记错误页、挑战页或拒绝访问文本。",
    "content_sha256": "完整输出的 SHA-256，用于核验报告预览与原始结果一致。",
    "unique_line_ratio": "去重后的非空规范化行数除以全部非空行数，用于诊断重复模板。",
    "word_f1": "仅对含完整人工正文的案例计算：供应商输出与标准正文的词袋 precision/recall 调和平均值。",
}


def word_overlap(predicted: str, reference: str) -> tuple[float, float, float]:
    predicted_words = Counter(re.findall(r"\w+", predicted.casefold()))
    reference_words = Counter(re.findall(r"\w+", reference.casefold()))
    if not reference_words:
        return (1.0, 1.0, 1.0) if not predicted_words else (0.0, 1.0, 0.0)
    if not predicted_words:
        return 0.0, 0.0, 0.0
    overlap = sum((predicted_words & reference_words).values())
    precision = overlap / sum(predicted_words.values())
    recall = overlap / sum(reference_words.values())
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def normalize_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}))
    return re.sub(r"\s+", " ", normalized).strip()


def evaluate_content(
    content: str,
    expected: list[str],
    *,
    required_facts: list[dict[str, Any]] | None = None,
    forbidden_any: list[str] | None = None,
    min_content_chars: int = 1,
    fact_coverage_threshold: float = 1.0,
    ground_truth_main_content: str = "",
) -> dict[str, Any]:
    folded = normalize_for_match(content)
    matched = [marker for marker in expected if normalize_for_match(marker) in folded]
    facts = required_facts or []
    fact_results = []
    for index, fact in enumerate(facts):
        alternatives = [str(item) for item in fact.get("alternatives", [])]
        hits = [item for item in alternatives if normalize_for_match(item) in folded]
        fact_results.append({
            "id": str(fact.get("id") or f"fact-{index + 1}"),
            "alternatives": alternatives,
            "matched": hits,
            "pass": bool(hits),
        })
    fact_hits = sum(item["pass"] for item in fact_results)
    fact_coverage = round(fact_hits / len(fact_results), 4) if fact_results else 1.0
    forbidden = [item for item in (forbidden_any or []) if normalize_for_match(item) in folded]
    lines = [re.sub(r"\s+", " ", line).strip().casefold() for line in content.splitlines()]
    lines = [line for line in lines if line]
    unique_line_ratio = round(len(set(lines)) / len(lines), 4) if lines else 1.0
    legacy_pass = bool(matched) if expected else True
    length_pass = len(content) >= min_content_chars
    content_pass = legacy_pass and length_pass and fact_coverage >= fact_coverage_threshold and not forbidden
    word_precision = word_recall = word_f1 = None
    if ground_truth_main_content:
        word_precision, word_recall, word_f1 = word_overlap(content, ground_truth_main_content)
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
        "word_precision": word_precision,
        "word_recall": word_recall,
        "word_f1": word_f1,
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
        transport_successful_elapsed = [float(item["elapsed_ms"]) for item in items if item["ok"]]
        content_successful_elapsed = [
            float(item["elapsed_ms"])
            for item in items
            if item["ok"] and item["metrics"]["content_pass"]
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
            "mean_word_f1": _mean_present(item["metrics"].get("word_f1") for item in items),
            "p50_ms": percentile(content_successful_elapsed, 0.50),
            "p95_ms": percentile(content_successful_elapsed, 0.95),
            "transport_p50_ms": percentile(transport_successful_elapsed, 0.50),
            "transport_p95_ms": percentile(transport_successful_elapsed, 0.95),
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


def _mean_present(values: Any) -> float | None:
    present = [float(value) for value in values if value is not None]
    return round(statistics.mean(present), 4) if present else None


def _failure_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        if not item["ok"]:
            counts[item.get("error_type") or "unknown_transport_failure"] += 1
        elif not item["metrics"]["content_pass"]:
            counts["content_validation_failure"] += 1
    return counts
