from __future__ import annotations

import re
import html
import json
import math
import hashlib
import statistics
import unicodedata
from collections import Counter, defaultdict
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote


METRIC_DEFINITIONS = {
    "transport_success": "供应商完成请求，并将响应判为请求成功；它不代表正文正确。",
    "content_pass": "机器内容判定：显著标记、最小长度、事实组、错误页排除及案例声明的正文覆盖要求均通过；稳定内容与结构契约另由统一任务谓词合并。",
    "marker_recall": "命中的显著标记数除以该案例登记的显著标记总数。",
    "elapsed_ms": "客户端开始单次调用至任务交付物形成的墙钟耗时，包含网络、远端处理和本地提取；离线评分、持久化及调用前节流不计入。历史耗时保持原值，不补算未测开销。",
    "mean_content_ms": "仅对程序判断通过的调用计算平均耗时；失败、空壳和超时不进入该均值。",
    "deadline_overrun": "某次物理调用超过自己的调用预算，不计为按时完成；重试获得独立的同额预算，逻辑结果耗时累计。新调用由统一截止边界约束，历史记录仅据原始耗时判断，不追认硬截止执行。",
    "content_chars": "持久化供应商输出的 Unicode 字符数。",
    "markdown_headings": "输出中的 Markdown 标题行数量。",
    "markdown_links": "输出中目标为 HTTP(S) 的 Markdown 链接数量。",
    "html_tags": "输出中残留的类 HTML 标签数量。",
    "fact_coverage": "命中的预登记事实组除以全部必需事实组；同组可接受表达等价。",
    "forbidden_requirement_hits": "命中案例明确禁止的内容；命中任一项即任务未通过。",
    "forbidden_requirement_pass": "案例明确声明的禁止内容均未出现。",
    "diagnostic_hits": "命中供诊断的文字；不会单独使任务失败。",
    "blocked_page": "检测到独立的访问限制或人机验证提示；仅作诊断，不决定任务是否通过。",
    "blocked_page_hits": "检测到的独立提示标题或标记；仅作诊断，不作为失败依据。",
    "declared_truncation_hits": "供应商输出中精确命中的截断声明；正文仅在全部预登记区域均被完整覆盖时可通过，资源与结构任务仍按各自交付合同验收。",
    "content_sha256": "完整输出的 SHA-256，用于核验报告预览与原始结果一致。",
    "unique_line_ratio": "去重后的非空规范化行数除以全部非空行数，用于诊断重复模板。",
    "word_f1": "完整参考正文与输出的词语重合评分，同时衡量正文遗漏和额外噪声；无完整参考的案例不计算。",
    "resource_recall": "从网页输出或结构化元数据中发现的预登记资源数，除以该案例全部必需资源数；输入 URL 本身不计为发现。",
    "resource_precision": "返回的任务范围内资源地址中，属于预登记目标或其允许变体的比例；显式 target-only 合同还会拒绝任何范围内的非目标资源。",
}


URL_KEYS = {"url", "href", "src", "link"}
RESOURCE_FILE_SUFFIXES = (
    'pdf','doc','docx','xls','xlsx','ppt','pptx','zip','tar','gz','bz2','7z',
    'png','jpg','jpeg','gif','webp','svg','avif','mp4','webm','mov','m4v','mp3','wav','ogg',
)
IMAGE_FILE_SUFFIXES = ('png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'avif')


def normalize_resource_url(value: str, base_url: str) -> str | None:
    value = value.strip().strip("<>\"'")
    # Markdown serializers may escape punctuation inside literal HTML or URL
    # text.  Those backslashes are presentation syntax, not URL bytes.
    value = re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])', r'\1', value)
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
    # Query parameters and paths may select different resources. Equivalence
    # belongs to task-declared aliases or patterns, never to a known website.
    netloc = parsed.netloc.lower()
    return urlunsplit((parsed.scheme.lower(), netloc, unquote(path), query, ""))


def _metadata_resource_candidates(
    value: Any, *, key: str = "", path: str = "metadata",
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            child_key = str(child_key)
            candidates.extend(_metadata_resource_candidates(
                child, key=child_key.casefold(), path=f"{path}.{child_key}",
            ))
    elif isinstance(value, list):
        for child in value:
            candidates.extend(_metadata_resource_candidates(child, key=key, path=path))
    elif isinstance(value, str) and (key in URL_KEYS or key.endswith(("links", "images", "videos", "media"))):
        candidates.append((value, path))
    return candidates


def _metadata_image_candidates(
    value: Any, *, path: str, image_branch: bool = False,
    accept_string: bool = False,
) -> list[tuple[str, str]]:
    """Return only provider fields explicitly typed as image answers."""
    candidates: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            child_key = str(child_key)
            folded = re.sub(r"[^a-z]", "", child_key.casefold())
            child_image_branch = image_branch or folded in {
                "image", "images", "imagelink", "imagelinks",
            }
            candidates.extend(_metadata_image_candidates(
                child, path=f"{path}.{child_key}",
                image_branch=child_image_branch,
                accept_string=child_image_branch and (
                    folded in URL_KEYS
                    or folded in {"image", "images", "imagelink", "imagelinks"}
                ),
            ))
    elif isinstance(value, list):
        for child in value:
            candidates.extend(_metadata_image_candidates(
                child, path=path, image_branch=image_branch,
                accept_string=image_branch,
            ))
    elif isinstance(value, str) and image_branch and accept_string:
        candidates.append((value, path))
    return candidates


def _markdown_image_destinations(content: str) -> list[str]:
    from .reference_quality import markdown_links
    destinations: list[str] = []
    pending = [content]
    while pending:
        for link in markdown_links(pending.pop()):
            if link.is_image:
                destinations.append(link.destination)
            if '[' in link.label:
                pending.append(link.label)
    return destinations


def _looks_like_image_url(value: str) -> bool:
    try:
        path = urlsplit(value).path
    except ValueError:
        return False
    return bool(re.search(rf"\.(?:{'|'.join(IMAGE_FILE_SUFFIXES)})$", path, re.I))


def _image_content_candidates(content: str) -> list[tuple[str, str]]:
    candidates = [(url, "content.markdown_image")
                  for url in _markdown_image_destinations(content)]
    candidates.extend((html.unescape(match[1]), "content.html_img") for match in re.findall(
        r"<img\b[^>]*?\bsrc\s*=\s*([\"'])(.*?)\1", content, re.I | re.S
    ))
    for match in re.findall(r"<img\b[^>]*?\bsrcset\s*=\s*([\"'])(.*?)\1", content, re.I | re.S):
        candidates.extend((html.unescape(part.strip().split()[0]), "content.html_img_srcset")
                          for part in match[1].split(',') if part.strip())
    # HTML attributes are decoded above. Do not also admit their raw entity
    # spelling as a second bare candidate; plain URL answers remain literal.
    bare_content = re.sub(r"<img\b[^>]*>", "", content, flags=re.I | re.S)
    # Explicit image markup is typed evidence. A standalone HTTP URL may also
    # be the entire answer, including an extensionless CDN address. Every URL
    # with that syntax is admitted alike, without consulting target resources.
    url_pattern = r"https?://[^\s<>'\")\]]+"
    bare_lines = {line.strip().strip('<>') for line in bare_content.splitlines()
                  if re.fullmatch(r"\s*<?" + url_pattern + r">?\s*", line)}
    candidates.extend((url, "content.bare_image_url") for url in
                      re.findall(url_pattern, bare_content)
                      if _looks_like_image_url(url) or url in bare_lines)
    return candidates


class _VisibleInlineFrames(HTMLParser):
    """Collect browser-serialized inline frame documents that are visibly embedded."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "iframe":
            return
        values = {key.casefold(): value for key, value in attrs}
        style = re.sub(r"\s+", "", values.get("style") or "").casefold()
        if (
            "hidden" in values
            or "display:none" in style
            or "visibility:hidden" in style
            or (values.get("width") == "0" and values.get("height") == "0")
        ):
            return
        source = values.get("srcdoc")
        if source:
            self.documents.append((source, values.get("data-moli-frame-url")))


def _inline_frame_candidates(
    content: str,
    *,
    base_url: str,
    images_only: bool,
) -> list[tuple[str, str]]:
    """Read resource URLs from visible native ``srcdoc`` serialization.

    Moli's official ``--with-frames`` output keeps child documents inside the
    returned HTML.  This is still provider output; the scorer only resolves
    URLs that the browser serialized, and ignores hidden synchronization or ad
    frames.  The same parser applies to any provider returning inline frames.
    """
    parser = _VisibleInlineFrames()
    parser.feed(content)
    candidates: list[tuple[str, str]] = []
    for document, declared_base in parser.documents[:100]:
        try:
            declared_base_is_http = bool(
                declared_base
                and urlsplit(declared_base).scheme in {"http", "https"}
            )
        except ValueError:
            declared_base_is_http = False
        frame_base = (
            declared_base
            if declared_base_is_http
            else base_url
        )
        if images_only:
            raw_candidates = _image_content_candidates(
                document,
            )
        else:
            raw_candidates = [
                (url, "content.frame_html_url")
                for _, url in re.findall(
                    r"(?:src|href)\s*=\s*([\"'])(.*?)\1", document, re.I
                )
            ]
            raw_candidates.extend(
                (url, "content.frame_bare_url")
                for url in re.findall(r"https?://[^\s<>'\")\]]+", document)
            )
        for raw_url, source in raw_candidates:
            normalized = normalize_resource_url(raw_url, frame_base)
            if normalized:
                candidates.append((normalized, source.replace("content.", "content.frame_", 1)))
    return candidates


def _native_json_resource_candidates(
    content: str,
    *,
    base_url: str,
) -> list[tuple[str, str]]:
    """Read URLs from a browser's native JSON page and network record.

    The accepted surface is deliberately structural: serialized page HTML and
    network entries named ``url`` or ``final_url``. It never reads expected
    answers to choose a field or invents a URL from a playback identifier.
    """
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    candidates: list[tuple[str, str]] = []
    html = payload.get("html")
    if isinstance(html, str):
        candidates.extend(
            (match[1], "content.native_json_html_url")
            for match in re.findall(
                r"(?:src|href)\s*=\s*([\"'])(.*?)\1", html, re.I
            )
        )
        candidates.extend(
            (url, "content.native_json_html_bare_url")
            for url in re.findall(r"https?://[^\s<>'\")\]]+", html)
        )
        candidates.extend(_inline_frame_candidates(
            html, base_url=base_url, images_only=False,
        ))
    pending = [payload.get("network")]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"url", "final_url"} and isinstance(item, str):
                    candidates.append((item, f"content.native_json_network.{key}"))
                elif isinstance(item, (dict, list)):
                    pending.append(item)
        elif isinstance(value, list):
            pending.extend(value)
    return candidates


def _normalized_candidates(
    candidates: list[tuple[str, str]], *, base_url: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    request_url = normalize_resource_url(base_url, base_url)
    sources: dict[str, set[str]] = defaultdict(set)
    for raw_url, source in candidates:
        url = normalize_resource_url(raw_url, base_url)
        if url and url != request_url:
            sources[url].add(source)
    urls = sorted(sources)
    return urls, [{"url": url, "sources": sorted(sources[url])} for url in urls]


def _visible_relative_resource_urls(content: str) -> list[str]:
    """Find visible file links omitted as hrefs by a Markdown serializer.

    Directory indexes and preformatted download pages may render a relative
    filename as ``<file%5Fname.zip>`` after the href is lost. Requiring both
    angle delimiters and a file-like suffix prevents ordinary prose and HTML
    tag names from becoming resource hits.
    """
    suffix = '|'.join(RESOURCE_FILE_SUFFIXES)
    pattern = rf'<((?:\.{{0,2}}/)?[^\s<>"\'()\[\]]+\.(?:{suffix})(?:\?[^\s<>"\'()\[\]]*)?)>'
    return re.findall(pattern, content, re.I)


def evaluate_resources(
    content: str,
    metadata: dict[str, Any],
    expected_resources: list[dict[str, Any]],
    *,
    base_url: str,
    eligible: bool = True,
    output_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score resource discovery separately from document text extraction.

    Only URLs explicitly returned in content or structured metadata are eligible.
    The request URL and provider final_url are deliberately excluded.
    """
    contract = output_contract or {}
    candidate_scope = contract.get("candidate_scope")
    reject_unexpected = contract.get("unexpected") == "reject"
    if contract and candidate_scope != "image_answer_fields":
        raise ValueError(f"Unsupported resource candidate_scope: {candidate_scope!r}")
    if contract and contract.get("unexpected") not in (None, "allow", "reject"):
        raise ValueError(f"Unsupported resource unexpected policy: {contract.get('unexpected')!r}")

    candidates: list[tuple[str, str]] = []
    projection = metadata.get('native_answer_projection') or {}
    typed_json_answer = (projection.get('content_format') == 'json'
                         and bool(projection.get('answer_fields')))
    if eligible and candidate_scope == "image_answer_fields":
        if "images" in metadata:
            candidates.extend(_metadata_image_candidates(
                metadata["images"], path="metadata.images", image_branch=True,
                accept_string=True,
            ))
        for key in ("media", "extras"):
            if key in metadata:
                candidates.extend(_metadata_image_candidates(metadata[key], path=f"metadata.{key}"))
        if not typed_json_answer:
            candidates.extend(_image_content_candidates(content))
            candidates.extend(_inline_frame_candidates(content, base_url=base_url, images_only=True))
    elif eligible:
        answer_fields = {key: metadata[key] for key in ('links','images','videos','media','extras') if key in metadata}
        candidates.extend(_metadata_resource_candidates(answer_fields))
        if not typed_json_answer:
            from .reference_quality import markdown_link_destinations
            candidates.extend((url, "content.bare_url") for url in
                              re.findall(r"https?://[^\s<>'\")\]]+", content))
            candidates.extend((match[1], "content.html_url") for match in
                              re.findall(r"(?:src|href)\s*=\s*([\"'])(.*?)\1", content, re.I))
            candidates.extend((url, "content.markdown_link")
                              for url in markdown_link_destinations(content))
            candidates.extend((url, "content.visible_file")
                              for url in _visible_relative_resource_urls(content))
            candidates.extend(_inline_frame_candidates(content, base_url=base_url, images_only=False))
            candidates.extend(_native_json_resource_candidates(content, base_url=base_url))
    normalized_candidates, candidate_evidence = _normalized_candidates(candidates, base_url=base_url)
    results = []
    allowed_urls: set[str] = set()
    for index, resource in enumerate(expected_resources):
        alternatives = [
            url for item in [*resource.get("urls", []), *resource.get("aliases", [])]
            if (url := normalize_resource_url(str(item), base_url))
        ]
        matched = sorted(set(alternatives) & set(normalized_candidates))
        patterns = resource.get('url_patterns', [])
        matched += [u for u in normalized_candidates
                    if any(re.search(p, u) for p in patterns) and u not in matched]
        allowed_urls.update(alternatives)
        allowed_urls.update(u for u in normalized_candidates if any(re.search(p, u) for p in patterns))
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
    allowed_candidates = set(normalized_candidates) & allowed_urls
    unexpected_urls = sorted(set(normalized_candidates) - allowed_urls)
    precision = (round(len(allowed_candidates) / len(normalized_candidates), 4)
                 if normalized_candidates else None)
    positive_pass = hits == total if total else None
    return {
        "resource_results": results,
        "resource_hits": hits,
        "resource_total": total,
        "resource_recall": round(hits / total, 4) if total else None,
        "resource_precision": precision,
        "resource_pass": (bool(positive_pass and not unexpected_urls)
                          if positive_pass is not None and reject_unexpected else positive_pass),
        "resource_candidate_urls": normalized_candidates,
        "resource_candidates": candidate_evidence,
        "unexpected_resource_urls": unexpected_urls,
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
    if ('acceptance_policy_version' in metrics or 'forbidden_hits' in metrics
            or ('blocked_page' in metrics and 'forbidden_requirement_hits' not in metrics)):
        raise ValueError('Historical scoring metrics require their frozen scorer or a complete offline rescore')
    if metrics.get('forbidden_requirement_hits') or metrics.get('forbidden_requirement_pass') is False:
        return False
    if (metrics.get('page_identity_required')
            and metrics.get('page_identity_status') != 'verified'):
        return False
    if not task_measured(metrics, task_kind) or metrics.get('deadline_overrun'):
        return False
    if "stable_task_pass" in metrics:
        combined = (metrics.get("content_pass")
                    if ("composite_acceptance_pass" in metrics
                        or metrics.get("body_region_results")
                        or metrics.get("pattern_results"))
                    else True)
        return bool(ok and metrics["stable_task_pass"]
                    and combined
                    and metrics.get("body_coverage_pass") is not False)
    field = {'structure':'list_task_pass', 'resource':'resource_pass', 'file':'raw_file_pass'}.get(task_kind, 'content_pass')
    return bool(ok and metrics.get(field))


def task_measured(metrics: dict, task_kind: str | None = None) -> bool:
    if metrics.get('evaluation_excluded'):
        return False
    if metrics.get('rule_review_status') == 'unresolved':
        return False
    kind = task_kind or metrics.get('task_kind', 'body')
    if (metrics.get('page_identity_required')
            and metrics.get('page_identity_status') == 'unavailable'
            and metrics.get('request_ok') is not False):
        # Combined pass flags already include identity. Only independent
        # failed requirements can prove failure while identity is unknown.
        definite_failure = bool(
            metrics.get('deadline_overrun')
            or any(error != 'page_identity_not_verified'
                   for error in metrics.get('stable_task_errors', []))
            or (kind == 'body' and (metrics.get('length_pass') is False
                                   or metrics.get('body_coverage_pass') is False))
        )
        if not definite_failure:
            return False
    return kind != 'file' or isinstance(metrics.get('raw_file_pass'), bool)


def normalize_for_match(value: str) -> str:
    from .reference_quality import clean_reference_text, visible_markdown_links
    normalized = unicodedata.normalize("NFKC", value).casefold()
    # Web editors insert zero-width spaces/BOMs and discretionary hyphens.
    # They are layout artifacts, not required prose; preserve meaningful joiners.
    normalized = clean_reference_text(normalized)
    # Compare visible inline Markdown text, not formatting or link destinations.
    normalized = visible_markdown_links(normalized)
    literal_pipe = '\ue000'
    while literal_pipe in normalized:
        literal_pipe += '\ue000'
    normalized = normalized.replace(r'\|', literal_pipe)
    # Native Markdown emitters may escape punctuation in visible text. These
    # escapes are presentation syntax, not part of the page's wording.
    normalized = re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\\\]^_`{|}~])', r'\1', normalized)
    normalized = re.sub(r'`([^`\n]+)`', r'\1', normalized)
    normalized = re.sub(r'\*\*(?=\S)([^*\n]*\S)\*\*', r'\1', normalized)
    def strip_single_emphasis(match: re.Match) -> str:
        inner = match.group(2)
        # Asterisks around words are Markdown; asterisks around numeric terms
        # can be multiplication or exponent notation and must remain literal.
        return inner if any(char.isalpha() for char in inner) else match.group(0)
    normalized = re.sub(r'(?<!\w)([*_])(?=\S)([^\n]*?\S)\1(?!\w)',
                        strip_single_emphasis, normalized)
    # Only a header/divider pair establishes a Markdown table. Pipe counts
    # alone also match footer separators and expressions such as a | b | c.
    table_rule = re.compile(r':?-{3,}:?')
    lines = normalized.splitlines()
    table_rows = set()
    divider_rows = set()
    for index in range(1, len(lines)):
        cells = [cell.strip() for cell in lines[index].split('|') if cell.strip()]
        if ('|' not in lines[index] or not cells
                or not all(table_rule.fullmatch(cell) for cell in cells)
                or '|' not in lines[index - 1]):
            continue
        divider_rows.add(index)
        table_rows.update((index - 1, index))
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].strip() and '|' in lines[cursor]:
            table_rows.add(cursor)
            cursor += 1
    table_lines = []
    for index, line in enumerate(lines):
        if index in divider_rows:
            continue
        if index in table_rows:
            line = re.sub(r'\s*\|\s*', ' ', line)
        table_lines.append(line)
    normalized = '\n'.join(table_lines).replace(literal_pipe, '|')
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
    # Preserve an existing quoted scalar's exact key/value while unifying
    # JSON and YAML notation before layout whitespace is folded away.
    def quoted_pair(match):
        import json
        key, scalar = match[1], match[2]
        value = scalar[1:-1].replace("''", "'")
        return json.dumps(key, ensure_ascii=False) + ": " + json.dumps(value, ensure_ascii=False)
    normalized = re.sub(r"(?m)^[ \t]*([a-z_][a-z0-9_.-]*)[ \t]*:[ \t]*('(?:[^'\n]|'')*')[ \t]*$",
                        quoted_pair, normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # A serializer may leave layout whitespace before sentence punctuation at
    # an inline-link boundary. Never bridge a digit to punctuation: doing so
    # could turn ``$10 .90`` into a different valid monetary value.
    normalized = re.sub(r"(?<!\d)\s+([,.;:!?])", r"\1", normalized)
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


def forbidden_in_text(text: str, phrase: str) -> bool:
    """Match ASCII word boundaries without segmenting CJK phrases."""
    if not phrase:
        return False
    left = r'(?<![a-z0-9_])' if phrase[0].isascii() and phrase[0].isalnum() else ''
    right = r'(?![a-z0-9_])' if phrase[-1].isascii() and phrase[-1].isalnum() else ''
    return bool(re.search(left + re.escape(phrase) + right, text))


def text_corruption(value: str) -> tuple[int, float, bool]:
    """Detect substantial UTF-8 replacement-character damage in text output."""
    count = value.count('\ufffd')
    ratio = count / max(1, len(value))
    return count, round(ratio, 4), count >= 20 and ratio >= 0.02


# These are provider-authored delivery notices, not ordinary page prose. Keep
# the patterns exact so a page discussing truncation is not rejected merely
# for containing the same words.
DECLARED_TRUNCATION_PATTERNS = (
    re.compile(
        r"(?im)^\s*\(Content truncated\. Fetch a more specific URL or section "
        r"for the full text\.\)\s*$"
    ),
    re.compile(r"(?im)^\s*\[Content truncated: showing \d+ of \d+ lines\]\s*$"),
)


def declared_truncation_hits(value: str) -> list[str]:
    """Return exact provider notices that explicitly declare incomplete text."""
    return [match.group(0).strip() for pattern in DECLARED_TRUNCATION_PATTERNS
            if (match := pattern.search(value))]


def code_matching_blocks(content: str) -> list[str]:
    """Keep literal code and independent block boundaries while ignoring layout."""
    if re.search(r'<(?:!doctype|html|body|pre)\b', content, re.I):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, 'html.parser')
        blocks = [node.get_text('', strip=False) for node in soup.find_all('pre')]
    else:
        text = re.sub(r'(?m)^[ \t]*(?:>[ \t]?)+', '', content)
        blocks, current, fence = [], [], None
        for line in text.splitlines(keepends=True):
            if fence is not None:
                if re.fullmatch(r'[ \t]*' + re.escape(fence[0])
                                + '{' + str(len(fence)) + r',}[ \t]*', line.rstrip('\r\n')):
                    blocks.append(''.join(current))
                    current, fence = [], None
                else:
                    current.append(line)
            elif match := re.match(r'^[ \t]*(`{3,}|~{3,})[^\r\n]*', line):
                fence = match[1]
        if fence is not None:
            blocks.append(''.join(current))
        if not blocks:
            text = html.unescape(text)
            from .body_coverage import native_markup_body_text
            if re.search(r'<(?:b|a|span)\b', text, re.I):
                text = native_markup_body_text(text, text)
            blocks = [text]
        else:
            blocks = [html.unescape(block) for block in blocks]
    return [re.sub(r'\s+', '', re.sub(r'(?m)^[ \t]*[0-9]{2,4}[ \t]+', '', block))
            for block in blocks]


def evaluate_content(
    content: str,
    expected: list[str],
    *,
    title: str | None = None,
    required_facts: list[dict[str, Any]] | None = None,
    forbidden_any: list[str] | None = None,
    diagnostic_any: list[str] | None = None,
    min_content_chars: int = 1,
    fact_coverage_threshold: float = 1.0,
    ground_truth_main_content: str = "",
    required_patterns: list[dict[str, Any]] | None = None,
    acceptance_contract: dict[str, Any] | None = None,
    base_url: str = "",
    named_links: list[dict] | None = None,
    body_requirements: dict | None = None,
    composite_acceptance: dict | None = None,
    allow_reference_block_exemptions: bool = False,
) -> dict[str, Any]:
    title_prefix = ((title.strip() + '\n') if title and title.strip() else '')
    visible_content = content
    from .reference_quality import is_html_document_or_fragment
    is_html = is_html_document_or_fragment(content)
    if is_html:
        # Keep raw HTML for records and links; score prose from visible text.
        from .dynamic_lists import visible_html_text
        visible_content = visible_html_text(content)
    else:
        from .reference_quality import visible_mixed_html_text
        visible_content = visible_mixed_html_text(content)
    has_html_prose = is_html or visible_content != content
    answer_text = title_prefix + visible_content
    from .reference_quality import blocked_page_hits, script_payload_shell
    block_hits = blocked_page_hits(answer_text)
    script_shell = script_payload_shell(answer_text)
    replacement_count, replacement_ratio, encoding_corruption = text_corruption(answer_text)
    truncation_hits = declared_truncation_hits(answer_text)
    folded = normalize_for_match(answer_text)
    content_folded = normalize_for_match(visible_content)
    matched = [marker for marker in expected if (text := normalize_for_match(marker)) and text in content_folded]
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
    block_reference_exemptions = []
    if allow_reference_block_exemptions and reference_folded:
        reference_block_terms = {
            normalize_for_match(hit) for hit in blocked_page_hits(ground_truth_main_content)
        }
        block_reference_exemptions = [
            hit for hit in block_hits
            if (term := normalize_for_match(hit)) and term in reference_block_terms
        ]
        block_hits = [hit for hit in block_hits if hit not in block_reference_exemptions]
    # Explicit forbidden text is a task requirement, not a challenge-page guess.
    # A reference mentioning it cannot silently remove the declared requirement.
    forbidden = [item for item in (forbidden_any or [])
                 if (text := normalize_for_match(item)) and forbidden_in_text(folded, text)]
    lines = [re.sub(r"\s+", " ", line).strip().casefold() for line in answer_text.splitlines()]
    lines = [line for line in lines if line]
    unique_line_ratio = round(len(set(lines)) / len(lines), 4) if lines else 1.0
    legacy_pass = bool(matched) if expected else True
    length_pass = len(visible_content) >= min_content_chars
    content_pass = (legacy_pass and length_pass and fact_coverage >= fact_coverage_threshold
                    and not forbidden
                    and not encoding_corruption
                    and not script_shell)
    pattern_results = []
    code_blocks = None
    for rule in required_patterns or []:
        scope = rule.get('match_scope', 'visible_text')
        if scope not in {'visible_text', 'text', 'output', 'code_compact'}:
            raise ValueError('Unknown required-pattern matching scope')
        if scope == 'code_compact' and code_blocks is None:
            code_blocks = code_matching_blocks(content)
        targets = code_blocks if scope == 'code_compact' else [content if scope == 'output' else visible_content]
        flags = 0 if scope == 'code_compact' else re.I
        count = len({match for target in targets for match in re.findall(rule['pattern'], target, flags)})
        pattern_results.append({'id':rule['id'],'count':count,'minimum':rule.get('minimum',1),'pass':count >= rule.get('minimum',1)})
    content_pass = content_pass and all(item['pass'] for item in pattern_results)
    from .body_coverage import evaluate_body_requirements, native_markup_body_text
    body_content = (native_markup_body_text(content, html.unescape(re.sub(r"<(script|style|template)\b[^>]*>.*?</\1\s*>", "", content, flags=re.I | re.S)))
                    if body_requirements and not is_html else visible_content)
    body_checks = evaluate_body_requirements(body_content, body_requirements or {})
    content_pass = content_pass and body_checks['body_coverage_pass']
    word_precision = word_recall = word_f1 = None
    if ground_truth_main_content:
        word_precision, word_recall, word_f1 = word_overlap(answer_text, ground_truth_main_content)
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
        "forbidden_requirement_hits": forbidden,
        "diagnostic_hits": [item for item in (diagnostic_any or [])
                            if (text := normalize_for_match(item)) and forbidden_in_text(folded, text)],
        "forbidden_requirement_pass": not forbidden,
        "blocked_page": bool(block_hits),
        "blocked_page_hits": block_hits,
        "blocked_page_reference_exemptions": block_reference_exemptions,
        "script_payload_shell": script_shell,
        "minimum_content_chars": min_content_chars,
        "length_pass": length_pass,
        "content_chars": len(content),
        "title_chars": len(title or ''),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "unique_line_ratio": unique_line_ratio,
        "markdown_headings": len(re.findall(r"(?m)^#{1,6}\s+", content)),
        "markdown_links": len(re.findall(r"\[[^]]+]\(https?://", content)),
        "html_tags": len(re.findall(r"<[^>]+>", content)),
        "replacement_character_count": replacement_count,
        "replacement_character_ratio": replacement_ratio,
        "text_encoding_corruption": encoding_corruption,
        "declared_truncation_hits": truncation_hits,
        "word_precision": word_precision,
        "word_recall": word_recall,
        "word_f1": word_f1,
        # Keep ``reference_sha256`` as the immutable run-time capture binding.
        # This separate hash says which reference bytes produced these derived
        # text metrics after an audited offline correction.
        "scored_reference_sha256": (hashlib.sha256(
            ground_truth_main_content.encode("utf-8")).hexdigest()
            if ground_truth_main_content else None),
    }
    if acceptance_contract:
        # Explicit body/record requirements own body-text acceptance. Stable
        # page-shell literals remain inspectable diagnostics in those cases.
        anchors_required = acceptance_contract.get("stable_text_role", "required") != "diagnostic"
        if not anchors_required and not (
            body_requirements
            or (composite_acceptance and composite_acceptance.get("groups"))
            or acceptance_contract.get("page_patterns")
        ):
            raise ValueError(
                "Diagnostic stable text requires body, composite, or page-pattern acceptance"
            )
        declared_body_pass = bool(result["body_coverage_pass"])
        declared_pattern_pass = all(item["pass"] for item in result["pattern_results"])
        from .stable_tasks import evaluate_stable_task
        # Document metadata remains diagnostic; it cannot supply rendered
        # page anchors, records, or declared body coverage.
        result.update(evaluate_stable_task(content, acceptance_contract,
                                           base_url, links=named_links,
                                            anchor_text=visible_content if has_html_prose else None,
                                           allow_closed_title_cards=True,
                                           anchors_required=anchors_required))
        if encoding_corruption:
            result["stable_task_pass"] = False
            result["stable_content_pass"] = False
            if "text_encoding_corruption" not in result["stable_task_errors"]:
                result["stable_task_errors"].append("text_encoding_corruption")
        if script_shell and acceptance_contract.get("schema") == "body_text":
            result["stable_task_pass"] = False
            result["stable_content_pass"] = False
            if "script_payload_shell" not in result["stable_task_errors"]:
                result["stable_task_errors"].append("script_payload_shell")
        # A ``body_text`` contract has no structural record-count floor, so
        # stable anchors must not let a tiny title/snippet bypass the declared
        # minimum amount of returned body text. Structured schemas retain their
        # record-based sufficiency rule.
        if acceptance_contract.get("schema") == "body_text" and not length_pass:
            result["stable_task_pass"] = False
            result["stable_content_pass"] = False
            if "content_too_short" not in result["stable_task_errors"]:
                result["stable_task_errors"].append("content_too_short")
        result["content_pass"] = bool(
            declared_body_pass and declared_pattern_pass and result["stable_task_pass"] and not forbidden
        )
        result["content_decision_basis"] = (
            "declared_content_requirements_with_stable_text_diagnostics"
            if not anchors_required else "stable_contract_and_declared_content_requirements")
    if composite_acceptance:
        from .composite_tasks import evaluate_composite_acceptance
        from .dynamic_lists import visible_html_text
        composite = evaluate_composite_acceptance(
            visible_html_text(content, preserve_links=True) if is_html else visible_content,
            composite_acceptance, base_url=base_url, links=named_links,
            native_html=content if is_html else None,
            table_content=content,
        )
        result.update(composite)
        result["content_pass"] = bool(result["content_pass"] and composite["composite_acceptance_pass"])
        result["content_decision_basis"] = "stable_and_composite_acceptance_contract"
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
