"""Provider-independent search contracts and metrics.

Anchor hits measure a declared known-page set, never exhaustive web recall.
Unjudged URLs are not irrelevant documents. Provider scores are not judgments.
"""

import hashlib
import json
import math
import os
import tempfile
import re
import statistics
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lexbench_webfetch.metrics import percentile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def request_cost(method, status, transport_exit, payload):
    """Billing follows the provider response, not local URL acceptance."""
    price = method.get("price_per_1000")
    if price is None or transport_exit:
        return None
    if status != 200:
        return 0.0
    if method["billing"] == "http-200-nonempty":
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            return None
        return price / 1000 if payload["results"] else 0.0
    return price / 1000


def canonical_url(url):
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Result URL must be an HTTP(S) URL without credentials")
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower()
        not in (
            "fbclid", "gclid", "_bhlid", "pscd", "ps_partner_key",
            "ps_xid", "gsxid", "gspk",
        )
    ]
    return urlunsplit(
        (p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", urlencode(query), "")
    )


def in_domain(url, domain):
    host = (urlsplit(url).hostname or "").lower()
    return host == domain.lower() or host.endswith("." + domain.lower())


def request_body(adapter, case, protocol, options=None):
    from .adapters import adapter_for
    return adapter_for(adapter).request(case, protocol, options)


def normalize(payload, adapter=None):
    from .adapters import normalize_envelope
    payload = normalize_envelope(payload, adapter)
    if (
        not isinstance(payload, dict)
        or payload.get("error")
        or not isinstance(payload.get("results"), list)
    ):
        raise ValueError("Response must contain a results array without an error")
    rows = []
    for rank, item in enumerate(payload["results"], 1):
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise ValueError("Each result requires a URL")
        canonical_url(item["url"])
        row = {"rank": rank, "url": item["url"]}
        for key in ("title", "snippet", "summary", "content"):
            value = item.get(key, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError("Invalid result text field: " + key)
            row[key] = value
        row["date"] = item.get("date", item.get("published_at"))
        rows.append(row)
    return rows


def evaluate(case, results):
    rows = results[:10]
    urls = [canonical_url(r["url"]) for r in rows]
    anchors = {canonical_url(u) for u in case.get("anchors", [])}
    aliases = {
        canonical_url(alias): canonical_url(target)
        for alias, target in case.get("identity_aliases", {}).items()
    }

    def matches(u):
        u = aliases.get(u, u)
        if u in anchors:
            return True
        parsed = urlsplit(u)
        if in_domain(u, "arxiv.org"):
            paper = re.fullmatch(
                r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?", parsed.path
            )
            if paper and paper[1] in case.get("paper_ids", []):
                return True
        if parsed.hostname == "github.com":
            repo = "/".join(parsed.path.strip("/").split("/")[:2]).lower()
            if repo in [r.lower() for r in case.get("repositories", [])]:
                return True
        return False

    ranks = [i + 1 for i, u in enumerate(urls) if matches(u)]
    qrels = {canonical_url(k): v for k, v in case.get("qrels", {}).items()}
    ndcg = None
    if qrels and all(u in qrels for u in urls):
        seen = set()
        gains = []
        for u in urls:
            gains.append(0 if u in seen else qrels[u])
            seen.add(u)

        def dcg(values):
            return sum((2**v - 1) / math.log2(i + 2) for i, v in enumerate(values))

        ideal = dcg(sorted(qrels.values(), reverse=True)[:10])
        ndcg = dcg(gains) / ideal if ideal else None
    domains = case.get("domains", [])
    metrics = {
        "anchor_hit_5": int(any(r <= 5 for r in ranks)) if anchors else None,
        "anchor_hit_10": int(bool(ranks)) if anchors else None,
        "reciprocal_rank": 1 / min(ranks) if ranks else (0 if anchors else None),
        "ndcg_10": ndcg,
        "domain_precision": sum(any(in_domain(u, d) for d in domains) for u in urls) / len(urls)
        if domains and urls
        else None,
        "duplicate_rate": 1 - len(set(urls)) / len(urls) if urls else None,
        "result_count": len(rows),
        "content_coverage": sum(bool(r["content"].strip()) for r in rows) / len(rows)
        if rows
        else None,
        "snippet_chars": sum(len(r["snippet"]) for r in rows),
        "summary_chars": sum(len(r["summary"]) for r in rows),
        "content_chars": sum(len(r["content"]) for r in rows),
    }
    if "reference_answer" in case:
        metrics.update(factual_evidence(case, results[:5]))
    return metrics


def answer_tokens(text):
    """Conservative lexical evidence, never semantic correctness or entailment."""
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())


def contains_answer(text, answer):
    wanted, actual = answer_tokens(answer), answer_tokens(text)
    return bool(wanted) and any(
        actual[i:i + len(wanted)] == wanted for i in range(len(actual) - len(wanted) + 1)
    )


def factual_evidence(case, results):
    sources = {canonical_url(u) for u in case.get("reference_urls", [])}
    source_ranks = [
        i for i, item in enumerate(results, 1) if canonical_url(item["url"]) in sources
    ]
    # Test fields individually so words spanning unrelated fields cannot form a hit.
    def hit(fields):
        if case.get("answer_match_rule") == "published-answers/2":
            from .answer_rules import match_reference_answer
            matcher = lambda text: match_reference_answer(case["reference_answer"], text)
        else:
            matcher = lambda text: contains_answer(text, case["reference_answer"])
        return int(any(
            matcher(item.get(field, ""))
            for item in results for field in fields
        ))
    return {
        "labeled_source_hit_5": int(bool(source_ranks)),
        "labeled_source_rr_5": 1 / min(source_ranks) if source_ranks else 0,
        "answer_in_preview_5": hit(("title", "snippet")),
        "answer_in_delivered_text_5": hit(("title", "snippet", "summary", "content")),
    }


def latency(values):
    return {
        "n": len(values),
        "mean": statistics.mean(values) if values else None,
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
    }


def aggregate(rows):
    output = {}
    for method in dict.fromkeys(r["method"] for r in rows):
        subset = [r for r in rows if r["method"] == method]
        success = [
            r for r in subset if r["http_status"] == 200 and r["results"] and not r.get("error")
        ]
        costs = [r["cost"] for r in subset]
        metrics = {}
        for key in (
            "anchor_hit_5",
            "anchor_hit_10",
            "reciprocal_rank",
            "ndcg_10",
            "domain_precision",
            "duplicate_rate",
            "content_coverage",
            "result_count",
            "snippet_chars",
            "summary_chars",
            "content_chars",
        ):
            values = [r["metrics"][key] for r in subset if r["metrics"].get(key) is not None]
            metrics[key] = {"n": len(values), "mean": statistics.mean(values) if values else None}
        output[method] = {
            "n": len(subset),
            "nonempty": len(success),
            "all_latency": latency([r["elapsed_ms"] for r in subset]),
            "success_latency": latency([r["elapsed_ms"] for r in success]),
            "metrics": metrics,
            "cost_total": sum(costs) if all(v is not None for v in costs) else None,
        }
    return output


def validate_profile(profile):
    if profile.get("schema") != "web-search/1":
        raise ValueError("Unsupported search profile")
    cases = profile["cases"]
    methods = profile["methods"]
    protocol = profile["protocol"]
    if not cases or not methods:
        raise ValueError("Empty matrix")
    for items in (cases, methods):
        ids = [x["id"] for x in items]
        if len(ids) != len(set(ids)) or any(not re.fullmatch("[a-z0-9-]+", v) for v in ids):
            raise ValueError("Duplicate or unsafe IDs")
    if type(protocol["top_k"]) is not int or protocol["top_k"] < 1 or protocol["rounds"] < 1 or protocol["concurrency"] != 1:
        raise ValueError("This protocol requires a positive result cutoff and serial calls")
    if protocol["score_round"] not in range(protocol["rounds"]):
        raise ValueError("Invalid score round")
    query_limit = 4096 if protocol.get("preserve_publisher_queries") else 128
    for method in methods:
        canonical_url(method["endpoint"])
        from .adapters import adapter_for
        adapter_for(method["adapter"])
        for case in cases:
            query = request_body(method["adapter"], case, protocol, method.get("output_options"))["query"]
            if not query.strip() or len(query) > query_limit:
                raise ValueError(
                    "Query exceeds publisher-preserving 4096 character contract"
                    if protocol.get("preserve_publisher_queries")
                    else "Query exceeds common 128 character contract"
                )
    for case in cases:
        for url in case.get("reference_urls", []):
            canonical_url(url)
        for alias, target in case.get("identity_aliases", {}).items():
            canonical_url(alias)
            if canonical_url(target) not in {canonical_url(u) for u in case.get("anchors", [])}:
                raise ValueError("Identity alias must refer to an existing target")
        for url in case.get("anchors", []):
            canonical_url(url)
            if protocol.get("reference_scope") == "identity":
                check = profile.get("reference_checks", {}).get(url)
                if (
                    not check
                    or not check.get("contains")
                    or any(not isinstance(s, str) or not s.strip() for s in check["contains"])
                ):
                    raise ValueError("Identity references require nonempty publisher assertions")
                canonical_url(check["url"])
        for value in case.get("qrels", {}).values():
            if type(value) is not int or not 0 <= value <= 3:
                raise ValueError("Relevance grades must be integers 0..3")


def verify_bundle(root, bindings):
    root = Path(root).resolve()
    for entry in bindings:
        rel = Path(entry["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Unsafe evidence path")
        path = root / rel
        if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
            raise ValueError("Symlink evidence is forbidden")
        if (
            not path.resolve().is_relative_to(root)
            or not path.is_file()
            or digest(path.read_bytes()) != entry["sha256"]
        ):
            raise ValueError("Evidence hash mismatch: " + entry["path"])


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": path.name, "sha256": digest(path.read_bytes())}


def task_acceptance(case, row, metric="primary", *, quality_excluded=False, cutoff=5):
    if row is None:
        return None
    if quality_excluded and "reference_answer" in case and metric in ("primary", "source", "answer"):
        return None
    if metric == "primary":
        metric = "answer" if "reference_answer" in case else "source" if case.get("anchors") else "site" if case.get("domains") and case.get("family") in ("site", "vertical") else "unscored"
    if metric == "availability":
        return not row.get("error") and row.get("http_status", 200) == 200 and bool(row.get("results"))
    if metric == "site":
        return (any(any(in_domain(item["url"], d) for d in case["domains"]) for item in row.get("results", [])[:cutoff]) if case.get("domains") else None)
    if metric == "unscored":
        return None
    if metric not in ("answer", "source"):
        raise ValueError("Unknown comparison metric")
    if "reference_answer" in case:
        from .evidence_quality import factual_sensitivity
        metrics = factual_sensitivity(case, row.get("results", []), cutoff=cutoff)["metrics"]
        return bool(metrics["answer_in_delivered_text_5"] if metric == "answer" else metrics["labeled_source_hit_5"])
    if metric == "answer" or not case.get("anchors"):
        return None
    key = "anchor_hit_5" if cutoff == 5 else "anchor_hit_10" if cutoff == 10 else None
    value = row.get("metrics", {}).get(key) if key else None
    if value is not None:
        return bool(value)
    safe = [r for r in row.get("results", [])[:cutoff] if str(r.get("url", "")).startswith(("https://", "http://"))]
    return bool(evaluate(case, safe)["anchor_hit_10"])


def classify(case, row):
    """Primary acceptance and diagnostic labels share the same task predicate."""
    if row.get("error") or row.get("http_status", 200) != 200:
        return "fail", "api_error"
    if not row["results"]:
        return "fail", "empty"
    accepted = task_acceptance(case, row)
    category = "answer" if "reference_answer" in case else "target" if case.get("anchors") else "domain" if case.get("domains") else "probe"
    return (("pass" if accepted else "fail"), category + ("_hit" if accepted else "_miss")) if accepted is not None else ("probe", "probe")
