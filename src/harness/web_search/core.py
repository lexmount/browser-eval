"""Provider-independent search contracts and metrics.

Anchor hits measure a declared known-page set, never exhaustive web recall.
Unjudged URLs are not irrelevant documents. Provider scores are not judgments.
"""

import re
import statistics
from pathlib import Path
from urllib.parse import urljoin

from lexbench_webfetch.metrics import percentile
from lexbench_websearch.contracts import validate_cases
from lexbench_websearch.scoring import canonical_url, normalize_results, task_acceptance

from harness.record_io import bytes_sha256, json_bytes
from harness.reporting.publication import atomic_write
from harness.web_search.adapters import BILLING_POLICIES, Adapter, adapter_for, normalize_envelope


def request_cost(method, status, transport_exit, payload):
    """Billing follows the provider response, not local URL acceptance."""
    adapter = adapter_for(method["adapter"]) if method.get("adapter") else Adapter(None)
    return adapter.cost(method, status, transport_exit, payload)


def request_body(adapter, case, protocol, options=None):
    return adapter_for(adapter).request(case, protocol, options)


def normalize(payload, adapter=None, top_k=None, *, base_url=None, project_slots=False,
              preserve_invalid_slots=False):
    payload = normalize_envelope(payload, adapter, top_k=top_k, project_slots=project_slots,
                                 preserve_invalid_slots=preserve_invalid_slots)
    if isinstance(payload, dict) and payload.get('error'):
        raise ValueError('Search response contains an error')
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        pages = payload["results"][:top_k] if project_slots and top_k is not None else payload["results"]
        projected = []
        for rank, item in enumerate(pages, 1):
            try:
                if isinstance(item, dict):
                    item = dict(item)
                    if base_url and isinstance(item.get("url"), str):
                        item["url"] = urljoin(base_url, item["url"])
                    if adapter == "browserbase" and "snippet" not in item:
                        item["snippet"] = item.get("description", "")
                    if "date" not in item and "published_at" not in item:
                        item["date"] = item.get("publishedDate")
                if preserve_invalid_slots:
                    normalized = normalize_results({'results': [item]})[0]
                    normalized['rank'] = rank
                    projected.append(normalized)
                else:
                    projected.append(item)
            except (ValueError, TypeError, AttributeError):
                if not preserve_invalid_slots:
                    raise
                projected.append(dict(rank=rank, url=f'https://invalid-result-slot.invalid/{rank}',
                                      title='', snippet='', summary='', content='', date=None,
                                      invalid_slot=True))
        payload = dict(payload, results=projected)
        if preserve_invalid_slots:
            return projected
    results = normalize_results(payload)
    return results[:top_k] if top_k is not None else results


def normalize_response(payload, method, protocol, request=None):
    """Project native slots using the frozen request's URL-resolution policy."""
    error = adapter_for(method["adapter"]).account_error(payload, method)
    if error:
        raise ValueError(error)
    adapter = adapter_for(method['adapter'])
    if method.get('expected_response_version') is not None and not (adapter.failure and adapter.failure(payload)):
        version = payload
        for key in adapter_for(method['adapter']).version_path:
            version = version.get(key) if isinstance(version, dict) else None
        if version is None:
            raise ValueError('Search response is missing its edition version')
    policy = protocol.get("result_url_resolution")
    if policy not in (None, "request-url/1", "request-url/2"):
        raise ValueError("Unknown native result URL policy")
    base_url = (request or {}).get("url") if policy else None
    if base_url:
        canonical_url(base_url)
    return normalize(payload, method["adapter"], protocol["top_k"] if protocol.get("normalize_top_k") else None,
                     base_url=base_url, project_slots=bool(policy),
                     preserve_invalid_slots=policy == 'request-url/2')


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
            r for r in subset if task_acceptance({}, r, 'availability')
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


def execution_resources(profile):
    """Bind provider lanes and credentials shared by multiple method variants."""
    methods = profile["methods"]
    policy = profile["execution_policy"]
    supplied = policy.get("assignments", {})
    if not isinstance(supplied, dict) or (supplied and set(supplied) != {m["id"] for m in methods}):
        raise ValueError("Execution resource assignments must cover every search method")
    accounts = {}
    for method in methods:
        accounts.setdefault(method["key_env"], []).append(method["id"])
    bindings = {}
    for method in methods:
        required = {method["id"]}
        if len(accounts[method["key_env"]]) > 1:
            required.add("account/" + method["key_env"])
        resources = supplied.get(method["id"], sorted(required))
        if (not isinstance(resources, list) or not resources or not all(isinstance(r, str) for r in resources) or len(set(resources)) != len(resources)
            or not required.issubset(resources) or any(r not in policy["resources"] for r in resources)):
            raise ValueError("Declare every provider and shared account execution resource")
        bindings[method["id"]] = tuple(resources)
    if {r for group in bindings.values() for r in group} != set(policy["resources"]):
        raise ValueError("Execution policy contains unused search resources")
    return bindings


def validate_profile(profile, *, execution_methods=None):
    if profile.get("schema") != "web-search/1":
        raise ValueError("Unsupported search profile")
    cases = profile["cases"]
    methods = profile["methods"]
    protocol = profile["protocol"]
    reference_retries = protocol.get("reference_retries", 0)
    if type(reference_retries) is not int or not 0 <= reference_retries <= 3:
        raise ValueError("Reference retries must be an integer between zero and three")
    if not cases or not methods:
        raise ValueError("Empty matrix")
    for items in (cases, methods):
        ids = [x["id"] for x in items]
        if len(ids) != len(set(ids)) or any(not re.fullmatch("[a-z0-9-]+", v) for v in ids):
            raise ValueError("Duplicate or unsafe IDs")
    if type(protocol["top_k"]) is not int or protocol["top_k"] not in (5, 10) or protocol["rounds"] < 1:
        raise ValueError("This protocol requires a supported cutoff (5 or 10)")
    concurrency = protocol["concurrency"]
    if type(concurrency) is not int or not 1 <= concurrency <= 64:
        raise ValueError("Search concurrency must be an integer between 1 and 64")
    execution = profile.get("execution_policy")
    if concurrency != 1 or execution is not None:
        from harness.benchmark_execution import ExecutionPolicy

        policy = ExecutionPolicy.from_dict(execution)
        if policy.max_in_flight != concurrency or policy.backend != "thread":
            raise ValueError("Search execution policy differs from declared concurrency/backend")
        if not {m["id"] for m in methods}.issubset(policy.resources):
            raise ValueError("Search execution requires a resource lane per method")
        execution_resources({**profile, "methods": execution_methods or methods})
        order = protocol.get("order")
        if order != "bounded calls":
            raise ValueError("Concurrent search requires an explicit bounded execution order")
        if protocol.get("interval_s", 0) and any(policy.resources[m["id"]] != 1 for m in methods):
            raise ValueError("Post-call intervals require one in-flight call per search method")
    if protocol["score_round"] not in range(protocol["rounds"]):
        raise ValueError("Invalid score round")
    if protocol.get("order", "complete provider batches") not in {"complete provider batches", "bounded calls", "rotate methods by case", "rotate methods by case and round"}:
        raise ValueError("Unsupported execution order")
    query_limit = 4096 if protocol.get("preserve_publisher_queries") else 128
    for method in methods:
        canonical_url(method["endpoint"])
        adapter_for(method["adapter"])
        if method['adapter'] == 'tencent-wsa':
            expected = method.get('expected_response_version')
            edition = (method.get('output_options') or {}).get('edition', 'standard')
            if (not isinstance(expected, str) or not expected.strip()
                    or edition != expected):
                raise ValueError('Yuanbao requires an expected response version matching its edition')
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
            or bytes_sha256(path.read_bytes()) != entry["sha256"]
        ):
            raise ValueError("Evidence hash mismatch: " + entry["path"])


def write_json(path, value):
    atomic_write(Path(path), json_bytes(value))
