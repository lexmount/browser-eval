"""Provider-independent search contracts and metrics.

Anchor hits measure a declared known-page set, never exhaustive web recall.
Unjudged URLs are not irrelevant documents. Provider scores are not judgments.
"""

from lexbench_websearch.contracts import validate_cases
from lexbench_websearch.scoring import canonical_url

import hashlib
import json
import re
import statistics
from pathlib import Path

from lexbench_webfetch.metrics import percentile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def request_cost(method, status, transport_exit, payload):
    """Billing follows the provider response, not local URL acceptance."""
    from .adapters import Adapter, adapter_for
    adapter = adapter_for(method["adapter"]) if method.get("adapter") else Adapter(None)
    return adapter.cost(method, status, transport_exit, payload)


def request_body(adapter, case, protocol, options=None):
    from .adapters import adapter_for
    return adapter_for(adapter).request(case, protocol, options)


def normalize(payload, adapter=None):
    from .adapters import normalize_envelope
    from lexbench_websearch.scoring import normalize_results
    return normalize_results(normalize_envelope(payload, adapter))


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
    query_limit = 4096 if protocol.get("preserve_publisher_queries") else 128
    for method in methods:
        canonical_url(method["endpoint"])
        from .adapters import adapter_for, BILLING_POLICIES
        adapter_for(method["adapter"])
        if method.get("billing", "unknown") not in BILLING_POLICIES:
            raise ValueError("Unsupported billing policy")
        for case in cases:
            query = request_body(method["adapter"], case, protocol, method.get("output_options"))["query"]
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
    from harness.reporting.publication import atomic_write
    atomic_write(Path(path), (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
