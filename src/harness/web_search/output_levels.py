"""Bind native output roles and source evidence to the shared level projections."""

import json
from copy import deepcopy

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from harness.record_io import bytes_sha256
from harness.web_search.adapters import adapter_for
from harness.web_search.core import verify_bundle
from lexbench_websearch.outputs import project_rows, qualify_field
from lexbench_websearch.scoring import canonical_url


def output_profile(profile):
    value = deepcopy(profile)
    for method in value["methods"]:
        method["output_fields"] = adapter_for(method["adapter"]).field_map(method)
    return value


def load_admissions(path):
    """Read an explicitly selected source bundle with complete artifact hashes."""
    from pathlib import Path

    path = Path(path)
    bundle = json.loads(path.read_bytes())
    if bundle.get("schema") != "web-search-field-admissions/1":
        raise ValueError("Unsupported field admission bundle")
    bindings = list(bundle["receipts"].values()) + bundle["artifacts"]
    verify_bundle(path.resolve().parent, bindings)
    artifacts = {
        binding["path"]: (path.parent / binding["path"]).read_bytes()
        for binding in bundle["artifacts"]
    }
    receipts = {
        mid: json.loads((path.parent / binding["path"]).read_bytes())
        for mid, binding in bundle["receipts"].items()
    }
    if any(receipt.get("plan", {}).get("method") != mid for mid, receipt in receipts.items()):
        raise ValueError("Field admission method binding mismatch")
    return receipts, artifacts


def field_sample_plan(profile, rows, method_id):
    """Choose fifty distinct document URLs before looking at fetched page text."""
    selected = next(m for m in output_profile(profile)["methods"] if m["id"] == method_id)
    fields = selected["output_fields"]["extra_text"]
    if not fields:
        raise ValueError("This method declares no extra original text")
    candidates = {}
    for row in rows:
        if row["method"] != method_id:
            continue
        for item in row["results"][: profile["protocol"]["top_k"]]:
            text = "\n\n".join(item.get(field) or "" for field in fields)
            if item.get("invalid_slot") or not text.strip():
                continue
            url = canonical_url(item["url"], policy="document-url/2")
            sample = dict(
                url=item["url"], text=text, row_id=row["id"], native_evidence=row["evidence"]
            )
            key = bytes_sha256(json.dumps(sample, sort_keys=True, ensure_ascii=False).encode())
            if url not in candidates or key < candidates[url][0]:
                candidates[url] = key, sample
    ordered = sorted(candidates, key=lambda url: bytes_sha256(url.encode()))
    if len(ordered) < 50:
        raise ValueError("Field admission requires fifty distinct delivered URLs")
    return dict(
        schema="web-search-field-sample/1",
        method=method_id,
        adapter=selected["adapter"],
        output_options=selected.get("output_options") or {},
        fields=fields,
        samples=[candidates[url][1] for url in ordered[:50]],
    )


def _bound(artifacts, binding):
    raw = artifacts[binding["path"]]
    if bytes_sha256(raw) != binding["sha256"]:
        raise ValueError("Field admission source artifact hash mismatch")
    return raw


def verify_field_admission(profile, rows, receipt, artifacts):
    """Recompute admission from retained live-capture bytes, never a saved flag."""
    if receipt.get("schema") != "web-search-field-admission/1":
        raise ValueError("Unsupported field admission receipt")
    expected = field_sample_plan(profile, rows, receipt["plan"]["method"])
    if receipt["plan"] != expected:
        raise ValueError("Field admission sample differs from native outputs")
    pages = receipt.get("pages", [])
    indexed = {page["url"]: page for page in pages}
    if len(indexed) != len(pages) or set(indexed) != {s["url"] for s in expected["samples"]}:
        raise ValueError("Field admission requires every sampled page exactly once")
    samples = []
    for sample in expected["samples"]:
        page = indexed[sample["url"]]
        raw = _bound(artifacts, page["evidence"])
        capture = json.loads(_bound(artifacts, page["capture"]))
        request = capture.get("request", {})
        if (
            capture.get("sha256") != page["evidence"]["sha256"]
            or capture.get("http_status") != 200
            or capture.get("transport_exit")
            or request.get("maxAgeHours") != 0
            or sample["url"] not in request.get("urls", [])
            or not capture.get("completed_at")
        ):
            raise ValueError("Field admission requires a bound successful live capture")
        payload = json.loads(raw)
        statuses = [v for v in payload.get("statuses", []) if v.get("id") == sample["url"]]
        content = [v for v in payload.get("results", []) if v.get("id") == sample["url"]]
        if (
            len(statuses) != 1
            or statuses[0].get("status") != "success"
            or statuses[0].get("source") != "crawled"
            or len(content) != 1
            or not isinstance(content[0].get("text"), str)
            or not content[0]["text"].strip()
        ):
            raise ValueError("Missing live page evidence cannot classify an output field")
        soup = BeautifulSoup(MarkdownIt("default").render(content[0]["text"]), "html.parser")
        for node in soup.select("script,style"):
            node.decompose()
        samples.append(
            dict(
                url=sample["url"],
                text=sample["text"],
                page_text=soup.get_text(" ", strip=True),
                observation_sha256=page["evidence"]["sha256"],
            )
        )
    return dict(fields=expected["fields"], **qualify_field(samples))


def level_inputs(profile, rows, level, *, admissions=None, artifacts=None):
    """Derive participation from adapters and verified field evidence."""
    declared = output_profile(profile)
    qualified = None
    if level == "l2":
        qualified = {}
        for method in declared["methods"]:
            if not method["output_fields"]["extra_text"]:
                continue
            receipt = (admissions or {}).get(method["id"])
            if receipt is None:
                raise ValueError("Missing field admission receipt: " + method["id"])
            qualified[method["id"]] = verify_field_admission(
                profile, rows, receipt, artifacts or {}
            )
    return project_rows(declared, rows, level, qualified_fields=qualified)
