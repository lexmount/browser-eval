"""Provider-independent search contracts and metrics.

Anchor hits measure a declared known-page set, never exhaustive web recall.
Unjudged URLs are not irrelevant documents. Provider scores are not judgments.
"""

import hashlib
import json
import re
import statistics
from pathlib import Path
from urllib.parse import urljoin

from lexbench_webfetch.metrics import percentile
from lexbench_websearch.contracts import validate_cases
from lexbench_websearch.scoring import canonical_url, normalize_results

from harness.reporting.publication import atomic_write
from harness.web_search.adapters import BILLING_POLICIES, Adapter, adapter_for, normalize_envelope


def digest(data):
    return hashlib.sha256(data).hexdigest()


def request_cost(method, status, transport_exit, payload):
    """Billing follows the provider response, not local URL acceptance."""
    adapter = adapter_for(method["adapter"]) if method.get("adapter") else Adapter(None)
    return adapter.cost(method, status, transport_exit, payload)


def request_body(adapter, case, protocol, options=None):
    return adapter_for(adapter).request(case, protocol, options)


def normalize(payload, adapter=None, top_k=None, *, base_url=None, project_slots=False):
    payload = normalize_envelope(payload, adapter, top_k=top_k, project_slots=project_slots)
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        pages = payload["results"][:top_k] if project_slots and top_k is not None else payload["results"]
        projected = []
        for item in pages:
            if isinstance(item, dict):
                item = dict(item)
                if base_url and isinstance(item.get("url"), str):
                    item["url"] = urljoin(base_url, item["url"])
                if adapter == "browserbase" and "snippet" not in item:
                    item["snippet"] = item.get("description", "")
                if "date" not in item and "published_at" not in item:
                    item["date"] = item.get("publishedDate")
            projected.append(item)
        payload = dict(payload, results=projected)
    results = normalize_results(payload)
    return results[:top_k] if top_k is not None else results


def normalize_response(payload, method, protocol, request=None):
    """Project native slots using the frozen request's URL-resolution policy."""
    policy = protocol.get("result_url_resolution")
    if policy not in (None, "request-url/1"):
        raise ValueError("Unknown native result URL policy")
    base_url = (request or {}).get("url") if policy else None
    if base_url:
        canonical_url(base_url)
    return normalize(payload, method["adapter"], protocol["top_k"] if protocol.get("normalize_top_k") else None,
                     base_url=base_url, project_slots=bool(policy))


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
    if type(protocol["top_k"]) is not int or protocol["top_k"] not in (5, 10) or protocol["rounds"] < 1 or protocol["concurrency"] != 1:
        raise ValueError("This protocol requires a supported cutoff (5 or 10) and serial calls")
    if protocol["score_round"] not in range(protocol["rounds"]):
        raise ValueError("Invalid score round")
    if protocol.get("order", "complete provider batches") not in {"complete provider batches", "rotate methods by case", "rotate methods by case and round"}:
        raise ValueError("Unsupported execution order")
    query_limit = 4096 if protocol.get("preserve_publisher_queries") else 128
    for method in methods:
        canonical_url(method["endpoint"])
        adapter_for(method["adapter"])
        if method.get("billing", "unknown") not in BILLING_POLICIES:
            raise ValueError("Unsupported billing policy")
        for case in cases:
            body = request_body(method["adapter"], case, protocol, method.get("output_options"))
            query = body.get("query", body.get("q", case["query"]))
            if not query.strip() or len(query) > query_limit:
                raise ValueError(
                    "Query exceeds publisher-preserving 4096 character contract"
                    if protocol.get("preserve_publisher_queries")
                    else "Query exceeds common 128 character contract"
                )
    validate_cases(cases, reference_checks=profile.get("reference_checks"), reference_scope=protocol.get("reference_scope"))


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
    atomic_write(Path(path), (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
