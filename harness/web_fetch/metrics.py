from __future__ import annotations

import re
import math
import hashlib
import statistics
import unicodedata
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote


METRIC_DEFINITIONS = {
    "transport_success": "供应商完成请求，并将响应判为请求成功；它不代表正文正确。",
    "content_pass": "机器内容判定：显著标记、最小长度、事实组、错误页排除及案例声明的正文覆盖要求均通过；稳定内容与结构契约另由统一任务谓词合并。",
    "marker_recall": "命中的显著标记数除以该案例登记的显著标记总数。",
    "elapsed_ms": "客户端调用至最终内容及必要转换完成的墙钟耗时，包含网络、远端处理和本地提取；离线评分与持久化不计入。历史耗时保持原值，不补算未测开销。",
    "mean_content_ms": "仅对程序判断通过的调用计算平均耗时；失败、空壳和超时不进入该均值。",
    "deadline_overrun": "实际墙钟耗时超过调用预算，不计为按时完成；新调用由统一截止边界约束，历史记录仅据原始耗时判断，不追认硬截止执行。",
    "content_chars": "持久化供应商输出的 Unicode 字符数。",
    "markdown_headings": "输出中的 Markdown 标题行数量。",
    "markdown_links": "输出中目标为 HTTP(S) 的 Markdown 链接数量。",
    "html_tags": "输出中残留的类 HTML 标签数量。",
    "fact_coverage": "命中的预登记事实组除以全部必需事实组；同组可接受表达等价。",
    "forbidden_hits": "输出中出现的预登记错误页、挑战页或拒绝访问文本。",
    "content_sha256": "完整输出的 SHA-256，用于核验报告预览与原始结果一致。",
    "unique_line_ratio": "去重后的非空规范化行数除以全部非空行数，用于诊断重复模板。",
    "word_f1": "完整参考正文与输出的词语重合评分，同时衡量正文遗漏和额外噪声；无完整参考的案例不计算。",
    "resource_recall": "从网页输出或结构化元数据中发现的预登记资源数，除以该案例全部必需资源数；输入 URL 本身不计为发现。",
}


URL_KEYS = {"url", "href", "src", "link"}


def normalize_resource_url(value: str, base_url: str) -> str | None:
    value = value.strip().strip("<>\"'")
    if not value or value.startswith(("data:", "blob:", "javascript:", "mailto:")):
        return None
    try:
        absolute = urljoin(base_url, value)
        parsed = urlsplit(absolute)
    except ValueError:
        # Provider text may contain URL-like prose such as an unmatched IPv6
        # bracket. A malformed optional resource candidate must not abort the
        # primary page-content evaluation.
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path,query=parsed.path,parsed.query
    # Same asset at different presentation sizes. Never drop signed query
    # parameters on arbitrary hosts or collapse distinct resources globally.
    if parsed.hostname in {'images.unsplash.com','plus.unsplash.com','images.pexels.com','images-assets.nasa.gov','www.nasa.gov'}:
        query=''
    if parsed.hostname and parsed.hostname.endswith('wikimedia.org') and '/thumb/' in path:
        path=path.replace('/thumb/','/').rsplit('/',1)[0];query=''
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower().replace('thumb.wikimedia.org','upload.wikimedia.org'), unquote(path), query, ""))


def _metadata_resource_urls(value: Any, *, key: str = "") -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            urls.extend(_metadata_resource_urls(child, key=str(child_key).casefold()))
    elif isinstance(value, list):
        for child in value:
            urls.extend(_metadata_resource_urls(child, key=key))
    elif isinstance(value, str) and (key in URL_KEYS or key.endswith(("links", "images", "videos", "media"))):
        urls.append(value)
    return urls


def evaluate_resources(
    content: str,
    metadata: dict[str, Any],
    expected_resources: list[dict[str, Any]],
    *,
    base_url: str,
    eligible: bool = True,
) -> dict[str, Any]:
    """Score resource discovery separately from document text extraction.

    Only URLs explicitly returned in content or structured metadata are eligible.
    The request URL and provider final_url are deliberately excluded.
    """
    answer_fields = {key: metadata[key] for key in ('links','images','videos','media','extras') if key in metadata}
    candidates = _metadata_resource_urls(answer_fields) if eligible else []
    if eligible:
        candidates.extend(re.findall(r"https?://[^\s<>'\")\]]+", content))
        candidates.extend(match[1] for match in re.findall(r"(?:src|href)\s*=\s*([\"'])(.*?)\1", content, re.I))
        candidates.extend(re.findall(r"\[[^\]]*\]\(([^)]+)\)", content))
    normalized_candidates = sorted({url for item in candidates if (url := normalize_resource_url(item, base_url))})
    normalized_candidates = [url for url in normalized_candidates if url != normalize_resource_url(base_url,base_url)]
    results = []
    for index, resource in enumerate(expected_resources):
        alternatives = [
            url for item in resource.get("urls", [])
            if (url := normalize_resource_url(str(item), base_url))
        ]
        matched = sorted(set(alternatives) & set(normalized_candidates))
        matched += [u for u in normalized_candidates if any(re.search(p,u) for p in resource.get('url_patterns',[])) and u not in matched]
        results.append({
            "id": str(resource.get("id") or f"resource-{index + 1}"),
            "kind": str(resource.get("kind") or "resource"),
            "expected_urls": alternatives,
            "expected_mime_types": [str(item) for item in resource.get("mime_types", [])],
            "matched_urls": matched,
            "pass": bool(matched),
        })
    hits = sum(item["pass"] for item in results)
    total = len(results)
    return {
        "resource_results": results,
        "resource_hits": hits,
        "resource_total": total,
        "resource_recall": round(hits / total, 4) if total else None,
        "resource_pass": hits == total if total else None,
        "resource_candidate_urls": normalized_candidates,
    }


def word_overlap(predicted: str, reference: str) -> tuple[float, float, float]:
    # CJK characters are individual tokens, not an entire unsegmented paragraph.
    # Strip Markdown link destinations/formatting from both sides: punctuation
    # and link spelling must not masquerade as missing prose.
    def tokens(value):
        from .reference_quality import clean_reference_text, visible_markdown_links
        value = unicodedata.normalize('NFKC', clean_reference_text(value)).casefold()
        value = visible_markdown_links(value)
        return Counter(re.findall(r'[\u3400-\u9fff]|[^\W_\u3400-\u9fff]+', value))
    predicted_words = tokens(predicted)
    reference_words = tokens(reference)
    if not reference_words:
        return (1.0, 1.0, 1.0) if not predicted_words else (0.0, 1.0, 0.0)
    if not predicted_words:
        return 0.0, 0.0, 0.0
    overlap = sum((predicted_words & reference_words).values())
    precision = overlap / sum(predicted_words.values())
    recall = overlap / sum(reference_words.values())
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def task_passed(ok: bool, metrics: dict, task_kind: str | None = None) -> bool:
    # Scored records carry the case kind so dataset-free summaries use the same
    # predicate as the report. Explicit case context still takes precedence.
    task_kind = task_kind if task_kind is not None else metrics.get('task_kind', 'body')
    if not task_measured(metrics, task_kind) or metrics.get('deadline_overrun'):
        return False
    if "stable_task_pass" in metrics:
        return bool(ok and metrics["stable_task_pass"] and not metrics.get("forbidden_hits")
                    and not metrics.get("stable_blocked_page"))
    field = {'structure':'list_task_pass', 'resource':'resource_pass', 'file':'raw_file_pass'}.get(task_kind, 'content_pass')
    return bool(ok and metrics.get(field))


def task_measured(metrics: dict, task_kind: str | None = None) -> bool:
    kind = task_kind or metrics.get('task_kind', 'body')
    return kind != 'file' or isinstance(metrics.get('raw_file_pass'), bool)


def normalize_for_match(value: str) -> str:
    from .reference_quality import clean_reference_text, visible_markdown_links
    normalized = unicodedata.normalize("NFKC", value).casefold()
    # Web editors insert zero-width spaces/BOMs and discretionary hyphens.
    # They are layout artifacts, not required prose; preserve meaningful joiners.
    normalized = clean_reference_text(normalized)
    # Compare visible inline Markdown text, not formatting or link destinations.
    normalized = visible_markdown_links(normalized)
    # Native Markdown emitters may escape punctuation in visible text. These
    # escapes are presentation syntax, not part of the page's wording.
    normalized = re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\\\]^_`{|}~])', r'\1', normalized)
    normalized = re.sub(r'`([^`\n]+)`', r'\1', normalized)
    normalized = re.sub(r'\*\*(?=\S)([^*\n]*\S)\*\*', r'\1', normalized)
    normalized = normalized.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-"}))
    # CJK text can be split at inline DOM boundaries. English word boundaries,
    # signs, digits and punctuation are deliberately retained.
    # Start only at a whitespace-run boundary: a long run not followed by CJK
    # must not be rescanned from every suffix (quadratic backtracking).
    normalized = re.sub(r'(?<=[\u3400-\u9fff])\s+|(?<!\s)\s+(?=[\u3400-\u9fff])', '', normalized)
    # Menu breadcrumbs commonly lose spaces when inline links are extracted.
    # Keep the separator itself (and all operands); do not collapse >=, signs
    # or English word boundaries. Fold layout newlines on both sides first:
    # plain-text DOM extraction may put every menu label on a separate line.
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # Inline styling often separates a currency symbol from its amount.
    # Normalize only currency/financing syntax, never digit or decimal boundaries.
    normalized = re.sub(r'([$£€¥]) +(?=[+-]?\d)', r'\1', normalized)
    normalized = re.sub(r'\bor(?=\d+ *[x×] *[$£€¥][+-]?\d)', 'or ', normalized)
    normalized = re.sub(r'\b(\d+) *[x×] *(?=[$£€¥][+-]?\d)', r'\1 x ', normalized)
    return re.sub(r'(?<=\w) *> *(?=\w)', '>', normalized)


def fact_in_text(text: str, fact: str) -> bool:
    """Match an explicitly registered normalized fact without fuzzy semantics."""
    if not fact:
        return False
    left = r'(?<![0-9.+\-])' if fact[0].isdigit() or fact[0] in '$£€¥' else ''
    right = r'(?![0-9]|\.[0-9])' if fact[-1].isdigit() else ''
    for match in re.finditer(left + re.escape(fact) + right, text):
        if fact[0] in '$£€¥' and text[:match.start()].rstrip().endswith(('-', '+')):
            continue
        return True
    return False


def evaluate_content(
    content: str,
    expected: list[str],
    *,
    required_facts: list[dict[str, Any]] | None = None,
    forbidden_any: list[str] | None = None,
    min_content_chars: int = 1,
    fact_coverage_threshold: float = 1.0,
    ground_truth_main_content: str = "",
    required_patterns: list[dict[str, Any]] | None = None,
    acceptance_contract: dict[str, Any] | None = None,
    base_url: str = "",
    named_links: list[dict] | None = None,
    body_requirements: dict | None = None,
) -> dict[str, Any]:
    folded = normalize_for_match(content)
    matched = [marker for marker in expected if (text := normalize_for_match(marker)) and text in folded]
    facts = required_facts or []
    from .dynamic_entry_matching import matching_entry_facts
    entry_hits = matching_entry_facts(folded, facts, normalize_for_match, fact_in_text)
    fact_results = []
    for index, fact in enumerate(facts):
        alternatives = [str(item) for item in fact.get("alternatives", [])]
        identifier = str(fact.get("id") or f"fact-{index + 1}")
        hits = (entry_hits[identifier] if identifier in entry_hits else
                [item for item in alternatives if fact_in_text(folded, normalize_for_match(item))])
        fact_results.append({
            "id": identifier,
            "alternatives": alternatives,
            "matched": hits,
            "pass": bool(hits),
            **({'match_scope': 'entry'} if identifier in entry_hits else {}),
        })
    fact_hits = sum(item["pass"] for item in fact_results)
    fact_coverage = round(fact_hits / len(fact_results), 4) if fact_results else 1.0
    # A reference can legitimately explain HTTP errors or CAPTCHA handling.
    # Such text is not evidence that the returned document is an error page.
    # Keep length, facts and structural gates active, including for short errors.
    reference_folded = normalize_for_match(ground_truth_main_content)
    reference_exemptions = [item for item in (forbidden_any or [])
                            if reference_folded and (text := normalize_for_match(item)) and text in reference_folded]
    if acceptance_contract:
        # A historical snapshot cannot exempt an explicit stable-mode block rule.
        reference_exemptions = []
    forbidden = [item for item in (forbidden_any or [])
                 if item not in reference_exemptions and (text := normalize_for_match(item)) and text in folded]
    lines = [re.sub(r"\s+", " ", line).strip().casefold() for line in content.splitlines()]
    lines = [line for line in lines if line]
    unique_line_ratio = round(len(set(lines)) / len(lines), 4) if lines else 1.0
    legacy_pass = bool(matched) if expected else True
    length_pass = len(content) >= min_content_chars
    content_pass = legacy_pass and length_pass and fact_coverage >= fact_coverage_threshold and not forbidden
    pattern_results = []
    for rule in required_patterns or []:
        count = len(set(re.findall(rule['pattern'], content, re.I)))
        pattern_results.append({'id':rule['id'],'count':count,'minimum':rule.get('minimum',1),'pass':count >= rule.get('minimum',1)})
    content_pass = content_pass and all(item['pass'] for item in pattern_results)
    from .body_coverage import evaluate_body_requirements
    body_checks = evaluate_body_requirements(content, body_requirements or {})
    content_pass = content_pass and body_checks['body_coverage_pass']
    word_precision = word_recall = word_f1 = None
    if ground_truth_main_content:
        word_precision, word_recall, word_f1 = word_overlap(content, ground_truth_main_content)
    result = {
        **body_checks,
        "content_pass": content_pass,
        "pattern_results": pattern_results,
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
        "reference_term_exemptions": reference_exemptions,
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
    if acceptance_contract:
        from .stable_tasks import evaluate_stable_task
        result.update(evaluate_stable_task(content, acceptance_contract, base_url, links=named_links))
        if forbidden:
            result["stable_task_pass"] = False
            result["stable_content_pass"] = False
            result["stable_task_errors"].append("forbidden_content")
        result["content_pass"] = result["stable_task_pass"]
        result["content_decision_basis"] = "stable_acceptance_contract"
    return result


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if fraction == 0.5:
        return round(statistics.median(ordered), 2)
    # Nearest rank: at least the requested fraction is at or below this value.
    index = max(0, min(len(ordered)-1, math.ceil(len(ordered)*fraction)-1))
    return round(ordered[index], 2)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Task-aware summaries using the same predicate as execution and reports."""
    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_provider[record["provider"]].append(record)
        by_case[record["case_id"]].append(record)

    provider_summary = {}
    for provider, items in sorted(by_provider.items()):
        evaluated = [item for item in items if task_measured(item['metrics'])]
        elapsed = [float(item["elapsed_ms"]) for item in items]
        transport_successful_elapsed = [float(item["elapsed_ms"]) for item in items if item["ok"]]
        content_successful_elapsed = [
            float(item["elapsed_ms"])
            for item in items
            if task_passed(item["ok"], item["metrics"])
        ]
        passed = sum(task_passed(item["ok"], item["metrics"]) for item in items)
        provider_summary[provider] = {
            "provider_type": items[0]["provider_type"],
            "attempts": len(items),
            "transport_success_rate": round(sum(item["ok"] for item in items) / len(items), 4),
            "task_evaluated_attempts": len(evaluated),
            "task_unmeasured_attempts": len(items) - len(evaluated),
            "task_pass_rate": round(passed / len(evaluated), 4) if evaluated else None,
            "mean_marker_recall": round(
                statistics.mean(item["metrics"]["marker_recall"] for item in items), 4
            ),
            "mean_fact_coverage": round(
                statistics.mean(item["metrics"].get("fact_coverage", item["metrics"]["marker_recall"]) for item in items), 4
            ),
            "mean_word_f1": _mean_present(item["metrics"].get("word_f1") for item in items),
            "mean_resource_recall": _mean_present(item["metrics"].get("resource_recall") for item in items),
            "resource_pass_rate": _mean_present(
                1.0 if item["metrics"].get("resource_pass") else 0.0
                for item in items if item["metrics"].get("resource_pass") is not None
            ),
            "p50_ms": percentile(content_successful_elapsed, 0.50),
            "p95_ms": percentile(content_successful_elapsed, 0.95),
            "mean_content_ms": round(statistics.mean(content_successful_elapsed), 2) if content_successful_elapsed else None,
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
        evaluated = [item for item in items if task_measured(item['metrics'])]
        passed = sum(task_passed(item["ok"], item["metrics"]) for item in items)
        case_summary[case_id] = {
            "name": items[0]["case_name"],
            "category": items[0]["category"],
            "attempts": len(items),
            "transport_success_rate": round(sum(item["ok"] for item in items) / len(items), 4),
            "task_evaluated_attempts": len(evaluated),
            "task_unmeasured_attempts": len(items) - len(evaluated),
            "task_pass_rate": round(passed / len(evaluated), 4) if evaluated else None,
        }
    return {"providers": provider_summary, "cases": case_summary}


def _mean_present(values: Any) -> float | None:
    present = [float(value) for value in values if value is not None]
    return round(statistics.mean(present), 4) if present else None


def _failure_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        if not task_measured(item['metrics']):
            continue
        if not item["ok"]:
            counts[item.get("error_type") or "unknown_transport_failure"] += 1
        elif not task_passed(item["ok"], item["metrics"]):
            counts["content_validation_failure"] += 1
    return counts
