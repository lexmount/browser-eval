"""Pinned public inputs, immutable compatibility calls and upstream scoring."""

from __future__ import annotations

import ast
import base64
import fcntl
import gzip
import json
import math
import os
import re
import runpy
import signal
import tempfile
import time
import uuid
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

from harness.data_home import data_home as shared_data_home
from harness.distribution import repository_root
from harness.record_io import bytes_sha256 as digest
from harness.record_io import read_json as shared_read_json
from harness.web_fetch.config import load_runtime_env
from harness.web_fetch.providers.http_client import request

ROOT = repository_root()
PROFILE = Path(__file__).resolve().parents[1] / "benchmarks/cards/zyte-product-extraction.json"
FIELDS = ("price", "sku", "availability", "InStock", "OutOfStock")
_RAW_SESSIONS = {}


def execution_identity(row):
    """Bind runtime evidence independently of recomputable field projections."""
    derived = {
        "normalized_fields",
        "collected_normalized_fields",
        "projection_revision",
        "physical_attempts",
    }
    value = {key: item for key, item in row.items() if key not in derived}
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def decoded_response(text):
    """Preserve malformed/nonfinite replies as text, so a completed call can checkpoint."""
    try:
        value = json.loads(text)

        def finite(item):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Nonfinite response number")
            if isinstance(item, dict):
                for child in item.values():
                    finite(child)
            elif isinstance(item, list):
                for child in item:
                    finite(child)

        finite(value)
        return value
    except ValueError:
        return {"non_json_response": text}


def read_json(path: Path):
    if path.is_symlink() or unsafe_parents(path):
        raise ValueError("Symlink inputs are prohibited")
    return shared_read_json(path)


def unsafe_parents(path: Path):
    # macOS exposes the system temporary roots through these fixed OS aliases.
    aliases = {Path("/tmp"), Path("/var")} if os.uname().sysname == "Darwin" else set()
    return any(p.is_symlink() and p not in aliases for p in path.parents)


def save_json(path: Path, data, *, exclusive=False):
    if path.is_symlink() or unsafe_parents(path):
        raise ValueError("Unsafe JSON output destination")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix="." + path.name + ".", delete=False
        ) as f:
            temporary = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def requires_fixture(method, options=None):
    options = options or {}
    return (
        available_methods()[method]["input"] == "url"
        or options.get("use_fixture_context")
        or options.get("input_route") == "url"
    )


def fixture_url(base, case_id, suffix=""):
    if not base or not base.startswith("https://") or suffix not in ("", ".html"):
        raise ValueError("Frozen fixture requires HTTPS and a declared path suffix")
    return f"{base.rstrip('/')}/{case_id}{suffix}"


def qualify_fixture(home, base, case_id, *, suffix="", timeout=60):
    url = fixture_url(base, case_id, suffix)
    for attempt in range(3):
        try:
            response = total_deadline(request)(
                url, headers={"Accept-Encoding": "gzip"}, timeout=timeout, max_bytes=10_000_000
            )
            break
        except (TimeoutError, OSError):
            if attempt == 2:
                raise
            time.sleep(2)
    body = (
        gzip.decompress(response.body)
        if response.headers.get("content-encoding") == "gzip"
        else response.body
    )
    expected = digest((home / "dataset/html" / (case_id + ".html")).read_bytes())
    if response.status != 200 or digest(body) != expected:
        raise ValueError("Remote fixture differs from frozen HTML: " + case_id)
    if response.url != url:
        raise ValueError("Use the canonical frozen fixture URL without redirects")
    return {
        "case_id": case_id,
        "sha256": expected,
        "http_status": response.status,
        "url": url,
        "qualification_fetch_attempts": attempt + 1,
    }


def data_home() -> Path:
    return shared_data_home() / "web-scraper"


def prepare(destination: Path, profile=PROFILE):
    require_external_output(destination)
    contract = read_json(profile)
    pin = contract["dataset"]
    target = destination / "dataset"
    if (target / "manifest.json").exists():
        return validate_dataset(destination, profile)
    if target.exists():
        raise ValueError("Incomplete dataset exists; retain it and use a new destination")
    if pin.get("distribution"):
        from harness.web_scraper.dataset import materialize

        materialize(destination, pin)
        return validate_dataset(destination, profile)
    raise ValueError("Dataset preparation requires a pinned Hugging Face distribution")


def validate_dataset(home: Path, profile=PROFILE):
    contract = read_json(profile)
    root = home / "dataset"
    manifest = read_json(root / "manifest.json")
    pin = contract["dataset"]
    inventory = digest(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    )
    if pin.get("inventory_sha256") and inventory != pin["inventory_sha256"]:
        from harness.web_scraper.dataset import verify_distribution

        verify_distribution(manifest, pin)
    if manifest["commit"] != pin["commit"] or manifest["cases"] != pin["cases"]:
        raise ValueError("Dataset is not the selected pinned cohort")
    for name, item in manifest["files"].items():
        path = root / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.is_symlink()
            or unsafe_parents(path)
        ):
            raise ValueError("Unsafe evidence path")
        if not path.is_file() or digest(path.read_bytes()) != item["sha256"]:
            raise ValueError("Dataset bytes changed: " + name)
    for name, expected in pin.get("files", {}).items():
        if manifest["files"].get(name, {}).get("sha256") != expected:
            raise ValueError("Pinned source binding changed")
    truth = read_json(root / "dataset/ground-truth.json")
    expected = {"html/" + key + ".html" for key in truth}
    if len(truth) != pin["cases"] or not expected.issubset(manifest["files"]):
        raise ValueError("Incomplete HTML population")
    return manifest


def official_scorer(home: Path, profile=PROFILE):
    validate_dataset(home, profile)
    # tabulate is used only by upstream's command-line presentation. Keep its
    # scoring functions unmodified and avoid adding an unrelated runtime dependency.
    source = (home / "dataset/evaluate.py").read_bytes()
    tree = ast.parse(source)
    tree.body = [
        n
        for n in tree.body
        if not (isinstance(n, ast.Import) and any(a.name == "tabulate" for a in n.names))
    ]
    scope = {"__name__": "pinned_upstream_scorer"}
    exec(compile(tree, "pinned_upstream_evaluate.py", "exec"), scope)
    return scope


def evaluate_predictions(truth: dict, predictions: dict, scorer, *, bootstrap=1000):
    if set(truth) != set(predictions):
        raise ValueError("Predictions must cover the exact declared population")
    return {
        field: scorer["evaluate"](
            truth,
            predictions,
            field,
            scorer["decimal_matching"] if field == "price" else scorer["hard_matching"],
            n_bootstrap=bootstrap,
            show_errors=False,
        )
        for field in FIELDS
    }


def reproduce(home: Path):
    require_external_output(home)
    scorer = official_scorer(home)
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    scores = {}
    contract = read_json(PROFILE)
    for method, pin in contract["historical_predictions"].items():
        path = home / "dataset" / pin["path"]
        if path.is_symlink() or unsafe_parents(path):
            raise ValueError("Unsafe historical prediction cache")
        if not path.exists():
            path = home / "historical-predictions" / (method + ".json")
            if path.is_symlink() or unsafe_parents(path):
                raise ValueError("Unsafe historical prediction cache")
            if not path.exists():
                source = contract["dataset"]
                url = f"https://raw.githubusercontent.com/{source['repository']}/{source['commit']}/{pin['path']}"
                response = request(url, timeout=60)
                if response.status != 200 or digest(response.body) != pin["sha256"]:
                    raise ValueError("Historical upstream prediction download changed")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.body)
        if path.is_symlink() or unsafe_parents(path) or digest(path.read_bytes()) != pin["sha256"]:
            raise ValueError("Historical upstream predictions changed")
        scores[method] = evaluate_predictions(truth, read_json(path), scorer)
    expected = read_json(PROFILE)["historical_f1"]
    for method, fields in expected.items():
        for field, value in fields.items():
            if round(scores[method][field]["f1"], 3) != value:
                raise ValueError("Historical result did not reproduce")
    result = {
        "kind": "upstream_historical_reproduction",
        "cases": len(truth),
        "scores": scores,
        "dataset_manifest_sha256": digest((home / "dataset/manifest.json").read_bytes()),
    }
    save_json(home / "historical-reproduction.json", result)
    return result


def probe_cases(truth: dict):
    """Select inputs before calls, without looking at provider predictions."""
    criteria = [
        lambda x: bool(x.get("InStock")) and bool(x.get("price")),
        lambda x: bool(x.get("OutOfStock")),
        lambda x: not x.get("price"),
    ]
    selected = []
    for predicate in criteria:
        matches = [key for key in sorted(truth) if key not in selected and predicate(truth[key])]
        if not matches:
            raise ValueError("Compatibility sampling stratum is absent")
        selected.append(matches[0])
    return selected


SCHEMA = {
    "type": "object",
    "properties": {
        "price": {
            "type": ["number", "null"],
            "description": (
                "Current price of the page's main product, not original price "
                "or a recommendation. Null if absent."
            ),
        },
        "sku": {
            "type": ["string", "null"],
            "description": "Main product SKU or unique product identifier. Null if absent.",
        },
        "availability": {
            "type": ["string", "null"],
            "enum": ["InStock", "OutOfStock", None],
            "description": "Main product stock status. Null if unknown.",
        },
    },
    "required": ["price", "sku", "availability"],
    "additionalProperties": False,
}


def available_methods():
    return METHODS


def baseline_extractor(home: Path):
    """Execute the hash-pinned upstream baseline, without its CLI entry point."""
    pin = read_json(PROFILE)["baseline_extractor"]
    source = home / pin["path"]
    bundled = home / "dataset" / pin["path"]
    if not source.exists() and bundled.exists():
        source = bundled
    if not source.exists():
        require_external_output(source)
        response = request(pin["url"], timeout=60)
        if response.status != 200 or digest(response.body) != pin["sha256"]:
            raise ValueError("Baseline source does not match the pinned upstream extractor")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(response.body)
    if (
        source.is_symlink()
        or unsafe_parents(source)
        or digest(source.read_bytes()) != pin["sha256"]
    ):
        raise ValueError("Pinned baseline source changed")
    return runpy.run_path(str(source), run_name="pinned_extruct_baseline")["extract"]


def redact(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if re.search(
                r"(?:^|[_-])(?:token|secret|authorization|password|cookies?|api[_-]?key)(?:$|[_-])",
                re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", k),
                re.I,
            )
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, str):
        credential_keys = {key for method in available_methods().values() for key in method["keys"]}
        for key, secret in os.environ.items():
            if secret and (
                key in credential_keys
                or (
                    any(
                        x in key
                        for x in ("KEY", "TOKEN", "PASSWORD", "SECRET", "ACCOUNT_ID", "CDP_URL")
                    )
                    and len(secret) >= 8
                )
            ):
                representations = {secret, quote(secret, safe=""), quote_plus(secret, safe="")}
                if key == "ZYTE_API_KEY":
                    representations.add(base64.b64encode((secret + ":").encode()).decode())
                for representation in representations:
                    if len(secret) < 8:
                        # Short credentials need token boundaries: a one-character key
                        # must not alter method IDs, hashes or unrelated error words.
                        value = re.sub(
                            r"(?<![\w])" + re.escape(representation) + r"(?![\w])",
                            "[REDACTED]",
                            value,
                        )
                    else:
                        value = value.replace(representation, "[REDACTED]")

    return value


def total_deadline(function):
    """Interrupt socket reads as well as connection setup on the serial Unix runner."""

    @wraps(function)
    def bounded(*args, timeout=120, **kwargs):
        def expired(signum, frame):
            raise TimeoutError("Native extraction wall-clock deadline exceeded")

        previous = signal.getsignal(signal.SIGALRM)
        started = time.monotonic()
        signal.signal(signal.SIGALRM, expired)
        old_timer = signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            return function(*args, timeout=timeout, **kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            if old_timer[0]:
                signal.setitimer(
                    signal.ITIMER_REAL,
                    max(0.000001, old_timer[0] - (time.monotonic() - started)),
                    old_timer[1],
                )

    return bounded


def validate_native_options(method, native_options):
    native_options = native_options or {}
    if set(native_options) - set(METHODS[method]["options"]):
        raise ValueError("Unsupported native extraction option")
    if native_options.get("input_route", "html") not in ("html", "url"):
        raise ValueError("Native input route must be html or url")
    for key in ("setJavaScriptEnabled", "discussion", "use_fixture_context", "norender"):
        if key in native_options and type(native_options[key]) is not bool:
            raise ValueError("Native extraction switches must be Boolean")
    if "rejectResourceTypes" in native_options:
        types = native_options["rejectResourceTypes"]
        if not isinstance(types, list) or any(
            t not in {"script", "stylesheet", "image", "font", "media"} for t in types
        ):
            raise ValueError("Unsupported static-fixture resource exclusion")
    if "rejectRequestPattern" in native_options:
        patterns = native_options["rejectRequestPattern"]
        if not isinstance(patterns, list) or any(not isinstance(p, str) for p in patterns):
            raise ValueError("Native request exclusions must be regex strings")
        for pattern in patterns:
            re.compile(pattern)
    return native_options


def _extruct_request(method, html, source_url, fixture_url, *, timeout, home, native_options):
    extract = baseline_extractor(home or data_home())
    started = time.monotonic()
    result = extract(html)
    return {
        "state": "response_received",
        "http_status": 200,
        "elapsed_seconds": time.monotonic() - started,
        "response": {"result": result},
        "request": {
            "input": "html",
            "endpoint": "local:extruct",
            "html_sha256": digest(html.encode()),
            "source_url": source_url,
        },
    }


def _cloudflare_request(method, html, source_url, fixture_url, *, timeout, home, native_options):
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/browser-rendering/json"
    headers = {"Authorization": "Bearer " + os.environ["CLOUDFLARE_API_TOKEN"]}
    body = {"html": html, "response_format": {"type": "json_schema", "json_schema": SCHEMA}}
    body.update({k: v for k, v in native_options.items() if k != "input_route"})
    if native_options.get("input_route") == "url":
        if not fixture_url:
            raise ValueError("Cloudflare URL extraction requires a frozen fixture")
        del body["html"]
        body["url"] = fixture_url
    return url, headers, body


def _browserbase_request(method, html, source_url, fixture_url, *, timeout, home, native_options):
    if not fixture_url:
        return {"state": "input_blocked", "error": "A frozen HTTPS snapshot URL is required"}
    url = "https://api.browserbase.com/v1/fetch"
    headers = {"X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"]}
    body = {"url": fixture_url, "format": "json", "schema": SCHEMA, "proxies": False}
    return url, headers, body


def _url_json_request(method, html, source_url, fixture_url, *, timeout, home, native_options):
    if not fixture_url:
        return {
            "state": "input_blocked",
            "error": (
                "Native endpoint requires a remotely reachable frozen HTML URL; "
                "original live URLs are prohibited."
            ),
        }
    url = (
        "https://api.firecrawl.dev/v2/scrape"
        if method == "firecrawl-json"
        else "https://webapi.oxylabs.io/v1/scrape"
    )
    headers = {
        "Authorization": "Bearer "
        + os.environ["FIRECRAWL_API_KEY" if method == "firecrawl-json" else "OXYLABS_API_KEY"]
    }
    body = (
        {
            "url": fixture_url,
            "formats": [{"type": "json", "schema": SCHEMA}],
            "onlyMainContent": False,
            "maxAge": 0,
        }
        if method == "firecrawl-json"
        else {
            "url": fixture_url,
            "output": ["json"],
            "json": {"schema": SCHEMA},
            "run_js": False,
        }
    )
    return url, headers, body


def _zyte_request(method, html, source_url, fixture_url, *, timeout, home, native_options):
    url = "https://api.zyte.com/v1/extract"
    headers = {
        "Authorization": "Basic "
        + base64.b64encode((os.environ["ZYTE_API_KEY"] + ":").encode()).decode()
    }
    body = {
        "url": source_url,
        "product": True,
        "productOptions": {"extractFrom": "userHtml", "userHtml": html},
    }
    return url, headers, body


def _scrapegraph_request(method, html, source_url, fixture_url, *, timeout, home, native_options):
    url = "https://v2-api.scrapegraphai.com/api/extract"
    headers = {"SGAI-APIKEY": os.environ["SGAI_API_KEY"]}
    body = {
        "html": html,
        "prompt": (
            "Extract the main product current price, SKU and stock availability. "
            "Use null for absent fields."
        ),
        "schema": SCHEMA,
    }
    return url, headers, body


def _raw_html_request(method, html, source_url, fixture_url, *, timeout, home, native_options):
    from urllib.parse import urlencode

    import requests

    endpoint = (
        "https://api.diffbot.com/v3/product"
        if method == "diffbot-product"
        else "https://api.scrapfly.io/extraction"
    )
    params = (
        {
            "token": os.environ["DIFFBOT_API_TOKEN"],
            "url": (fixture_url if native_options.get("use_fixture_context") else source_url),
        }
        if method == "diffbot-product"
        else {
            "key": os.environ["SCRAPFLY_API_KEY"],
            "url": source_url,
            "extraction_model": "product",
            "timeout": timeout,
        }
    )
    if method == "diffbot-product":
        if native_options.get("use_fixture_context") and not fixture_url:
            raise ValueError("Frozen fixture context requires a URL")
        if "discussion" in native_options:
            params["discussion"] = str(native_options["discussion"]).lower()
        if "norender" in native_options:
            params["norender"] = str(native_options["norender"]).lower()
    url = endpoint + "?" + urlencode(params)
    started = time.monotonic()
    session = _RAW_SESSIONS.get(method)
    if session is None:
        session = _RAW_SESSIONS[method] = requests.Session()
    with session.post(
        url,
        data=html.encode(),
        headers={"Content-Type": "text/html"},
        timeout=timeout,
        stream=True,
        allow_redirects=False,
    ) as response:
        raw = response.raw.read(10_000_001, decode_content=True)
        if len(raw) > 10_000_000:
            raise ValueError("Provider response exceeded limit")
        payload = decoded_response(raw.decode("utf-8", "replace"))
        return {
            "state": "response_received",
            "http_status": response.status_code,
            "elapsed_seconds": time.monotonic() - started,
            "response": redact(payload),
            "provider_usage": {
                k: response.headers.get(k)
                for k in ("X-Scrapfly-Api-Cost", "X-Scrapfly-Remaining-Api-Credit")
                if response.headers.get(k) is not None
            },
            "request": {
                "endpoint": endpoint,
                "input": "html",
                "html_sha256": digest(html.encode()),
                "source_url": source_url,
                "effective_context_url": params["url"],
                "fixture_url": fixture_url,
                "mode": "product",
                "native_options": native_options,
            },
        }


METHODS = {
    "firecrawl-json": {
        **{
            "keys": ["FIRECRAWL_API_KEY"],
            "input": "url",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": [],
        },
        "adapter": _url_json_request,
    },
    "cloudflare-json": {
        **{
            "keys": ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"],
            "input": "html",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": [
                "setJavaScriptEnabled",
                "rejectResourceTypes",
                "input_route",
                "rejectRequestPattern",
            ],
        },
        "adapter": _cloudflare_request,
    },
    "zyte-product": {
        **{
            "keys": ["ZYTE_API_KEY"],
            "input": "html",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": [],
        },
        "adapter": _zyte_request,
    },
    "diffbot-product": {
        **{
            "keys": ["DIFFBOT_API_TOKEN"],
            "input": "html",
            "minimum_call_interval_seconds": 13,
            "post_call_cooldown_seconds": 10,
            "options": ["discussion", "use_fixture_context", "norender"],
        },
        "adapter": _raw_html_request,
    },
    "scrapegraph-extract": {
        **{
            "keys": ["SGAI_API_KEY"],
            "input": "html",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": [],
        },
        "adapter": _scrapegraph_request,
    },
    "scrapfly-product": {
        **{
            "keys": ["SCRAPFLY_API_KEY"],
            "input": "html",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": [],
        },
        "adapter": _raw_html_request,
    },
    "oxylabs-json": {
        **{
            "keys": ["OXYLABS_API_KEY"],
            "input": "url",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": [],
        },
        "adapter": _url_json_request,
    },
    "extruct": {
        **{
            "keys": [],
            "input": "html",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": [],
        },
        "adapter": _extruct_request,
    },
    "browserbase-json": {
        **{
            "keys": ["BROWSERBASE_API_KEY"],
            "input": "url",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": [],
        },
        "adapter": _browserbase_request,
    },
}


@total_deadline
def provider_call(
    method, html, source_url, fixture_url=None, *, timeout=120, home=None, native_options=None
):
    """One native route per provider; no hidden fallback or quality retry."""
    native_options = validate_native_options(method, native_options)
    prepared = METHODS[method]["adapter"](
        method,
        html,
        source_url,
        fixture_url,
        timeout=timeout,
        home=home,
        native_options=native_options,
    )
    if isinstance(prepared, dict):
        return prepared
    url, headers, body = prepared
    started = time.monotonic()
    response = request(url, method="POST", headers=headers, json_body=body, timeout=timeout)
    payload = decoded_response(response.text())
    return {
        "state": "response_received",
        "http_status": response.status,
        "elapsed_seconds": time.monotonic() - started,
        "response": redact(payload),
        "provider_usage": {
            k: v
            for k, v in response.headers.items()
            if k.lower() in {"x-browser-ms-used", "x-browser-time", "x-browser-usage"}
        },
        "request": {
            "endpoint": url,
            "input": "url"
            if native_options.get("input_route") == "url"
            else available_methods()[method]["input"],
            "html_sha256": digest(html.encode()),
            "schema": SCHEMA,
            "fixture_url": fixture_url,
            "source_url": source_url,
            "native_options": native_options,
        },
    }


def normalize(method: str, payload: dict):
    """Map exposed fields only. Never infer answers from labels or source URLs."""
    if not isinstance(payload, dict) or payload.get("success") is False:
        return None
    if method == "extruct":
        item = payload.get("result")
        if not isinstance(item, dict):
            return None
        result = {f: str(item[f]) for f in FIELDS if item.get(f) is not None}
        stock = [s for s in ("InStock", "OutOfStock") if item.get(s) == s]
        if not result.get("availability") and len(stock) == 1:
            result["availability"] = stock[0]
        return result

    def mapping(value):
        return value if isinstance(value, dict) else {}

    if method == "firecrawl-json":
        item = mapping(payload.get("data")).get("json")
    elif method == "browserbase-json":
        status = payload.get("statusCode")
        if not isinstance(status, int) or not 200 <= status < 300:
            return None
        item = payload.get("content")
    elif method == "cloudflare-json":
        item = payload.get("result")
    elif method == "oxylabs-json":
        results = payload.get("results")
        item = (
            mapping(results[0]).get("json")
            if isinstance(results, list) and len(results) == 1
            else None
        )
    elif method == "scrapegraph-extract":
        item = payload.get("json")
    elif method == "scrapfly-product":
        p = payload.get("data") or mapping(
            mapping(payload.get("result")).get("extracted_data")
        ).get("data")
        if isinstance(p, dict):
            offers = p.get("offers")
            offer = mapping(offers[0]) if isinstance(offers, list) and offers else {}
            price = offer.get("price")
            if isinstance(price, dict):
                price = price.get("amount")
            stock = offer.get("availability")
            if stock is not None and not isinstance(stock, str):
                stock = None
            item = {
                "price": price,
                "sku": mapping(p.get("identifiers")).get("sku"),
                "availability": {
                    "available": "InStock",
                    "unavailable": "OutOfStock",
                    "sold_out": "OutOfStock",
                }.get(stock, stock),
            }
        else:
            item = None
    elif method == "zyte-product":
        p = payload.get("product")
        item = (
            {"price": p.get("price"), "sku": p.get("sku"), "availability": p.get("availability")}
            if isinstance(p, dict)
            else None
        )
    elif method == "diffbot-product":
        objects = payload.get("objects")
        p = mapping(objects[0]) if isinstance(objects, list) and len(objects) == 1 else {}
        offer = mapping(p.get("offerPriceDetails")).get("amount", p.get("offerPrice"))
        item = (
            {"price": offer, "sku": p.get("sku"), "availability": p.get("availability")}
            if p
            else None
        )
        if item and isinstance(item["availability"], bool):
            item["availability"] = "InStock" if item["availability"] else "OutOfStock"
    else:
        item = None
    if not isinstance(item, dict) or not any(f in item for f in ("price", "sku", "availability")):
        return None
    result = {}
    for field in ("price", "sku", "availability"):
        value = item.get(field)
        if value is not None and not isinstance(value, (dict, list, bool)):
            result[field] = str(value)
    if result.get("availability") in {"InStock", "OutOfStock"}:
        result[result["availability"]] = result["availability"]
    return result


def serve_fixtures(home: Path, *, port=8767):
    """Expose only the selected public snapshot files, without changing their bytes."""
    from functools import lru_cache
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    validate_dataset(home)
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    allowed = {key + ".html" for key in truth}

    @lru_cache(maxsize=140)
    def encoded(name, compressed):
        raw = (home / "dataset/html" / name).read_bytes()
        return gzip.compress(raw, mtime=0) if compressed else raw

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(send_body=True)

        def do_HEAD(self):
            self.respond(send_body=False)

        def respond(self, *, send_body):
            name = urlsplit(self.path).path.lstrip("/")
            if name not in allowed:
                self.send_error(404)
                return
            compressed = "gzip" in self.headers.get("Accept-Encoding", "")
            raw = encoded(name, compressed)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-transform")
            self.send_header("Vary", "Accept-Encoding")
            if compressed:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if send_body:
                self.wfile.write(raw)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def access_blocker(row):
    """Stop calls after evidenced authentication, permission or quota denial."""
    if row.get("state") == "input_blocked":
        return "input_configuration"
    if row.get("state") == "credentials_missing":
        return "credentials_missing"
    status = row.get("http_status")
    response = row.get("response")
    message = str(row.get("error", ""))
    if isinstance(response, dict):
        message += " ".join(
            str(response.get(key, ""))
            for key in ("error", "errors", "message", "non_json_response")
        )
    if status == 401:
        return "authentication"
    if status == 402:
        return "quota_or_payment"
    if status == 403:
        return "permission"
    native = row.get("response", {})
    native_error = isinstance(native, dict) and (
        native.get("success") is False or bool(native.get("error"))
    )
    if status == 429 or native_error:
        if re.search(
            r"invalid (?:api[ _-]?)?key|unauthorized|unauthenticated|"
            r"authentication failed|invalid token",
            message,
            re.I,
        ):
            return "authentication"
        if re.search(
            r"permission denied|access denied|forbidden|not authorized", message, re.I
        ) and not re.search(r"target page|download|fetch|session", message, re.I):
            return "permission"
        if re.search(
            r"quota|credits?|account balance|insufficient balance|free plan|usage period|"
            r"subscription|payment|account.*expired",
            message,
            re.I,
        ):
            return "quota_or_permission"
    return None


def require_external_output(path):
    path = Path(path)
    if path.is_symlink() or unsafe_parents(path):
        raise ValueError("Unsafe output destination")
    if path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Provider evidence must stay outside the source repository")


def probe(home: Path, methods: list[str], **options):
    """Run or resume one declared scope; never attach two workers to one run."""
    run = options.pop("run_dir", None)
    if run is None:
        run = (
            home
            / "runs"
            / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
        )
    run = Path(run)
    require_external_output(run)
    for name in (
        "execution.lock",
        "calls",
        "execution/pipeline.py",
        "calls.journal.jsonl",
        "contract.json",
        "inflight.json",
        "manifest.json",
        "remote-fixture-after.json",
    ):
        require_external_output(run / name)
    run.mkdir(parents=True, exist_ok=True)
    with (run / "execution.lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _probe_unlocked(home, methods, run_dir=run, **options)


def collect_calls(
    home,
    methods,
    case_ids,
    run,
    contract,
    *,
    fixture_base=None,
    fixture_suffix=".html",
    full=True,
    min_call_interval=0,
    post_call_cooldown=0,
    native_options=None,
    repair=False,
):
    """Own dispatch, pacing, checkpoints and retry accounting for every native run."""
    journal = run / "calls.journal.jsonl"
    if journal.is_symlink() or unsafe_parents(journal):
        raise ValueError("Symlink inputs are prohibited")
    registry = available_methods()
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    frozen_inputs = read_json(home / "dataset/manifest.json")["files"]
    prior = {}
    physical = []
    attempts = {}
    dispatched_attempts = {}
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe checkpoint evidence path")
            path = run / relative
            if digest(path.read_bytes()) != entry["sha256"]:
                raise ValueError("Checkpoint call changed")
            record = read_json(path)
            key = (record["method"], record["case_id"])
            prior[key] = entry
            physical.append(entry)
            attempts[key] = attempts.get(key, 0) + 1
            if record.get("state") not in ("credentials_missing", "input_blocked"):
                dispatched_attempts[key] = dispatched_attempts.get(key, 0) + 1
        verify_retry_history(
            run, {"calls": list(prior.values()), "physical_calls": physical}, contract
        )
    if {p.relative_to(run).as_posix() for p in (run / "calls").glob("*.json")} - {
        e["path"] for e in physical
    }:
        raise ValueError("Uncheckpointed call exists; preserve and reconcile it before resuming")
    inflight = run / "inflight.json"
    if inflight.exists():
        pending = read_json(inflight)
        completed = prior.get((pending["method"], pending["case_id"]))
        if (
            completed is None
            or read_json(run / completed["path"]).get("physical_attempt")
            != pending["physical_attempt"]
        ):
            raise ValueError(
                "Ambiguous repair dispatch requires reconciliation"
                if repair
                else "Ambiguous in-flight attempt; reconcile retained evidence before resuming"
            )
        inflight.unlink()
    records = []
    # Conservatively observe the full frozen pause after a cross-process resume.
    last_call_started = time.monotonic() if prior else None
    last_call_finished = last_call_started
    for ordinal, case_id in enumerate(case_ids):
        offset = ordinal % len(methods)
        for method in methods[offset:] + methods[:offset]:
            if (method, case_id) in prior:
                entry = prior[(method, case_id)]
                prior_row = read_json(run / entry["path"])
                if not (full and access_blocker(prior_row)):
                    records.append(entry)
                    continue
                if dispatched_attempts.get((method, case_id), 0) > contract["access_retry_limit"]:
                    raise PermissionError("Access remains blocked; declared retry budget exhausted")
            missing = [key for key in registry[method]["keys"] if not os.getenv(key)]
            raw = (home / "dataset/html" / (case_id + ".html")).read_bytes()
            if digest(raw) != frozen_inputs["html/" + case_id + ".html"]["sha256"]:
                raise ValueError("Frozen extraction HTML changed before dispatch")
            row = {
                "method": method,
                "case_id": case_id,
                "input_sha256": digest(raw),
                "kind": "failure_repair"
                if repair
                else ("structured_extraction_attempt" if full else "compatibility_probe"),
                "started_at": datetime.now(UTC).isoformat(),
            }
            if missing and full:
                raise PermissionError("Missing credentials before dispatch: " + ", ".join(missing))
            if missing:
                row.update(state="credentials_missing", missing_variables=missing)
            else:
                if last_call_started is not None:
                    remaining = max(
                        min_call_interval - (time.monotonic() - last_call_started),
                        post_call_cooldown - (time.monotonic() - last_call_finished),
                    )
                    while remaining > 0:
                        time.sleep(min(remaining, 30))
                        remaining = max(
                            min_call_interval - (time.monotonic() - last_call_started),
                            post_call_cooldown - (time.monotonic() - last_call_finished),
                        )
                save_json(
                    inflight,
                    {
                        "method": method,
                        "case_id": case_id,
                        "physical_attempt": attempts.get((method, case_id), 0) + 1,
                        "input_sha256": digest(raw),
                        "state": "dispatch_pending",
                    },
                    exclusive=True,
                )
                try:
                    last_call_started = time.monotonic()
                    row.update(
                        provider_call(
                            method,
                            raw.decode("utf-8"),
                            truth[case_id]["url"],
                            fixture_url(fixture_base, case_id, fixture_suffix)
                            if fixture_base
                            else None,
                            **(
                                {"home": home, "native_options": native_options, "timeout": 120}
                                if repair
                                else ({"home": home} if method == "extruct" else {})
                            ),
                        )
                    )
                    if "elapsed_seconds" not in row:
                        row["elapsed_seconds"] = time.monotonic() - last_call_started
                    normalized = normalize(method, row.get("response", {}))
                    row["normalized_fields"] = normalized
                    row["compatibility"] = (
                        "structured_output"
                        if normalized is not None and row.get("http_status") == 200
                        else "no_structured_output"
                    )
                except Exception as error:
                    row.update(
                        state="call_error",
                        error=redact(str(error)),
                        elapsed_seconds=time.monotonic() - last_call_started,
                        normalized_fields=None,
                        compatibility="no_structured_output",
                    )
                last_call_finished = time.monotonic()
            row["finished_at"] = datetime.now(UTC).isoformat()
            row.setdefault("state", "response_received")
            number = attempts.get((method, case_id), 0) + 1
            row["physical_attempt"] = number
            if number > 1:
                prior_row = read_json(run / prior[(method, case_id)]["path"])
                row["retry_execution_seconds"] = prior_row.get(
                    "elapsed_seconds", 0
                ) + prior_row.get("retry_execution_seconds", 0)
            suffix = "" if number == 1 else f"-attempt{number}"
            stem = case_id if repair else f"{method}-{case_id}"
            path = run / "calls" / f"{stem}{suffix}.json"
            if repair and number > 1:
                history = [
                    read_json(run / e["path"])
                    for e in physical
                    if (read_json(run / e["path"])["method"], read_json(run / e["path"])["case_id"])
                    == (method, case_id)
                ]
                row["physical_attempts"] = history + [dict(row)]
                row["retry_missing_timers"] = sum(r.get("elapsed_seconds") is None for r in history)
            save_json(path, redact(row), exclusive=True)
            entry = {"path": path.relative_to(run).as_posix(), "sha256": digest(path.read_bytes())}
            with journal.open("a", encoding="utf-8") as checkpoint:
                checkpoint.write(json.dumps(entry) + "\n")
                checkpoint.flush()
                os.fsync(checkpoint.fileno())
            if inflight.exists():
                inflight.unlink()
            records.append(entry)
            physical.append(entry)
            print(
                method,
                case_id[:10],
                row["state"],
                row.get("http_status", ""),
                row.get("compatibility", ""),
                flush=True,
            )
            blocker = access_blocker(row) if full else None
            if blocker:
                save_json(
                    run / "access-blocked.json",
                    {
                        "method": method,
                        "case_id": case_id,
                        "reason": blocker,
                        "call": entry,
                        "remaining_logical_cases": len(case_ids) * len(methods) - len(records),
                    },
                )
                raise PermissionError(
                    f"{method}: {blocker}; calls retained; repair access then resume the same run"
                )
    return records, physical


def _probe_unlocked(
    home: Path,
    methods: list[str],
    *,
    fixture_base=None,
    fixture_suffix=".html",
    full=False,
    largest=False,
    min_call_interval=0,
    post_call_cooldown=0,
    run_dir=None,
):
    if not isinstance(min_call_interval, (int, float)) or not 0 <= min_call_interval <= 3600:
        raise ValueError("Minimum call interval must be between 0 and 3600 seconds")
    if not isinstance(post_call_cooldown, (int, float)) or not 0 <= post_call_cooldown <= 3600:
        raise ValueError("Post-call cooldown must be between 0 and 3600 seconds")
    validate_dataset(home)
    load_runtime_env()
    registry = available_methods()
    if not methods or len(set(methods)) != len(methods) or any(m not in registry for m in methods):
        raise ValueError("Select supported methods explicitly")
    if fixture_base and urlsplit(fixture_base).scheme != "https":
        raise ValueError("Remote frozen fixtures require HTTPS")
    if fixture_suffix not in (".html", ""):
        raise ValueError("Frozen fixture suffix must be .html or empty")
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    if full and largest:
        raise ValueError("Full scope and a size probe are mutually exclusive")
    case_ids = (
        sorted(truth)
        if full
        else (
            [
                max(
                    sorted(truth),
                    key=lambda key: (home / "dataset/html" / (key + ".html")).stat().st_size,
                )
            ]
            if largest
            else probe_cases(truth)
        )
    )

    def verify_remote():
        def one(case_id):
            return qualify_fixture(home, fixture_base, case_id, suffix=fixture_suffix)

        if not fixture_base:
            return []
        return [one(case_id) for case_id in case_ids]

    run = Path(run_dir)
    continuing = (run / "contract.json").exists()
    run.mkdir(parents=True, exist_ok=True)
    remote_before = verify_remote()
    requested = {
        "schema_version": 1,
        "kind": "full_archived_extraction"
        if full
        else ("input_size_boundary_only" if largest else "input_compatibility_only"),
        "case_ids": case_ids,
        "methods": methods,
        "timeout_seconds": 120,
        "retries": 0,
        "access_resume_policy": (
            "Retain every call; after repairing access, retry blocked cases once "
            "and select the final response"
        ),
        "access_retry_limit": 1,
        "concurrency": 1,
        "minimum_call_interval_seconds": min_call_interval,
        "post_call_cooldown_seconds": post_call_cooldown,
        "method_order": "rotate_by_case_ordinal",
        "fixture_base": fixture_base,
        "fixture_suffix": fixture_suffix,
        "dataset_manifest_sha256": digest((home / "dataset/manifest.json").read_bytes()),
        "pipeline_sha256": digest(Path(__file__).read_bytes()),
        "baseline_extractor": read_json(PROFILE).get("baseline_extractor")
        if "extruct" in methods
        else None,
        "schema": SCHEMA,
        "remote_fixture_before": remote_before,
    }
    if continuing:
        contract = read_json(run / "contract.json")
        for field in (
            "kind",
            "case_ids",
            "methods",
            "minimum_call_interval_seconds",
            "post_call_cooldown_seconds",
            "fixture_base",
            "fixture_suffix",
            "dataset_manifest_sha256",
            "pipeline_sha256",
            "baseline_extractor",
        ):
            if contract[field] != requested[field]:
                raise ValueError("Resume contract changed: " + field)
        verify_execution_snapshot(run, contract)
        if (run / "manifest.json").exists():
            load_run(run)
            return run
    else:
        contract = requested
        snapshot = run / "execution/pipeline.py"
        if snapshot.exists() and digest(snapshot.read_bytes()) != requested["pipeline_sha256"]:
            raise ValueError("Pre-dispatch pipeline snapshot changed")
        save_json(run / "contract.json", contract, exclusive=True)
    records, physical = collect_calls(
        home,
        methods,
        case_ids,
        run,
        contract,
        fixture_base=fixture_base,
        fixture_suffix=fixture_suffix,
        full=full,
        min_call_interval=min_call_interval,
        post_call_cooldown=post_call_cooldown,
    )
    validate_dataset(home)
    if (
        "extruct" in methods
        and read_json(PROFILE).get("baseline_extractor") != contract["baseline_extractor"]
    ):
        raise ValueError("Baseline extractor binding changed during execution")
    receipt = verify_remote()
    after = run / "remote-fixture-after.json"
    if after.exists():
        old = read_json(after)

        def identity(items):
            return [(r["case_id"], r["sha256"], r["http_status"]) for r in items]

        if identity(old) != identity(receipt):
            raise ValueError("Existing final fixture receipt differs")
    else:
        save_json(after, receipt, exclusive=True)
    save_json(
        run / "manifest.json",
        {
            "kind": "compatibility_evidence",
            "contract_sha256": digest((run / "contract.json").read_bytes()),
            "calls": records,
            "physical_calls": physical,
            "remote_fixture_after_sha256": digest((run / "remote-fixture-after.json").read_bytes()),
        },
        exclusive=True,
    )
    return run


def verify_execution_snapshot(run, contract):
    relative = contract.get("pipeline_snapshot")
    if relative:
        path = Path(relative)
        snapshot = run / path
        if (
            path.is_absolute()
            or ".." in path.parts
            or snapshot.is_symlink()
            or not snapshot.is_file()
            or digest(snapshot.read_bytes()) != contract["pipeline_sha256"]
        ):
            raise ValueError("Frozen execution source changed")


def verify_retry_history(run, manifest, contract):
    """Require a complete physical sequence and its final logical selection."""
    entries = manifest.get("physical_calls", manifest["calls"])
    groups, latest = {}, {}
    methods = contract["methods"] if "methods" in contract else [contract["method"]]
    cases = contract["case_ids"] if "case_ids" in contract else contract["participant"]["case_ids"]
    scope = {(method, case) for method in methods for case in cases}
    paths = set()
    for entry in entries:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or entry["path"] in paths:
            raise ValueError("Invalid physical call inventory")
        paths.add(entry["path"])
        path = run / relative
        row = read_json(path)
        if digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Physical call evidence changed")
        key = (row["method"], row["case_id"])
        if key not in scope:
            raise ValueError("Physical call is outside the frozen method/case scope")
        sequence = groups.setdefault(key, [])
        attempt = row.get("physical_attempt", 1)
        if type(attempt) is not int or attempt != len(sequence) + 1:
            raise ValueError("Physical call attempt sequence is incomplete")
        sequence.append(row)
        latest[key] = entry
    selected = {}
    for entry in manifest["calls"]:
        row = read_json(run / entry["path"])
        key = (row["method"], row["case_id"])
        if key in selected:
            raise ValueError("Duplicate logical call selection")
        selected[key] = entry
    if selected != latest:
        raise ValueError("Logical call must select the final physical attempt")
    retries = contract.get("access_retry_limit", 0)
    for sequence in groups.values():
        dispatched = [
            row
            for row in sequence
            if row.get("state") not in ("credentials_missing", "input_blocked")
        ]
        if len(dispatched) > 1 + retries:
            raise ValueError("Physical retry budget exceeded")
        for row in sequence[:-1]:
            if row.get("compatibility") == "structured_output" or not (access_blocker(row)):
                raise ValueError("Retry is outside the declared access-repair policy")
        if len(sequence) > 1:
            elapsed = sum(row.get("elapsed_seconds") or 0 for row in sequence[:-1])
            if not math.isclose(
                sequence[-1].get("retry_execution_seconds", -1), elapsed, rel_tol=1e-9, abs_tol=1e-9
            ):
                raise ValueError("Retry execution time does not cover physical attempts")
    journal = run / "calls.journal.jsonl"
    if "physical_calls" in manifest and not journal.exists():
        raise ValueError("Physical call journal is missing")
    if journal.exists():
        if journal.is_symlink() or unsafe_parents(journal):
            raise ValueError("Symlink inputs are prohibited")
        raw = journal.read_bytes()
        if not raw.endswith(b"\n"):
            raise ValueError("Retry journal is incomplete")
        if [json.loads(line) for line in raw.splitlines()] != entries:
            raise ValueError("Physical inventory differs from retained journal")


def load_run(run: Path):
    manifest = read_json(run / "manifest.json")
    contract = read_json(run / "contract.json")
    verify_execution_snapshot(run, contract)
    if digest((run / "contract.json").read_bytes()) != manifest["contract_sha256"]:
        raise ValueError("Call contract changed")
    verify_retry_history(run, manifest, contract)
    if (
        manifest.get("remote_fixture_after_sha256")
        and digest((run / "remote-fixture-after.json").read_bytes())
        != manifest["remote_fixture_after_sha256"]
    ):
        raise ValueError("Remote fixture receipt changed")
    rows = []
    for entry in manifest["calls"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe call evidence path")
        path = run / relative
        if path.is_symlink() or digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Call evidence changed")
        rows.append(read_json(path))
    expected = {(m, c) for m in contract["methods"] for c in contract["case_ids"]}
    if len(rows) != len(expected) or {(r["method"], r["case_id"]) for r in rows} != expected:
        raise ValueError("Incomplete or duplicate compatibility matrix")
    return contract, rows


PROJECTION_REGRESSIONS = (
    "scrapfly-available-is-in-stock",
    "scrapfly-sold-out-is-out-of-stock",
    "scrapfly-unknown-stock-stays-unknown",
    "extruct-canonical-stock-indicator",
    "extruct-conflicting-indicators-stay-unknown",
    "extruct-explicit-stock-takes-precedence",
)


def check_projection_controls():
    for native, expected in (
        ("available", "InStock"),
        ("sold_out", "OutOfStock"),
        ("preorder", "preorder"),
    ):
        actual = normalize("scrapfly-product", {"data": {"offers": [{"availability": native}]}})
        if actual.get("availability") != expected:
            raise ValueError("Native stock projection regression failed")
        if native == "preorder" and any(f in actual for f in ("InStock", "OutOfStock")):
            raise ValueError("Unknown availability was promoted to known stock")
    for native, expected in (
        ({"InStock": "InStock"}, "InStock"),
        ({"OutOfStock": "OutOfStock"}, "OutOfStock"),
        ({"InStock": "InStock", "OutOfStock": "OutOfStock"}, None),
        ({"availability": "OutOfStock", "InStock": "InStock"}, "OutOfStock"),
        ({"InStock": True}, None),
        ({}, None),
    ):
        if normalize("extruct", {"result": native}).get("availability") != expected:
            raise ValueError("Native baseline stock indicator regression failed")


def score_run(home: Path, run: Path, *, reproject=False, output=None):
    """Score the declared population; retain empty predictions for failed calls."""
    require_external_output(Path(output) if output else run / "scores.json")
    if output and (
        Path(output).resolve().is_relative_to(ROOT.resolve())
        or Path(output).resolve().is_relative_to((run / "calls").resolve())
        or Path(output).resolve().is_relative_to((run / "execution").resolve())
    ):
        raise ValueError("Score revisions must stay outside Git and preserve physical evidence")
    if reproject and output is None:
        raise ValueError("A projection correction requires a separate output file")
    if reproject:
        check_projection_controls()
    contract, rows = load_run(run)
    validate_dataset(home)
    if contract["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Scoring input does not match call contract")
    all_truth = read_json(home / "dataset/dataset/ground-truth.json")
    truth = {key: all_truth[key] for key in contract["case_ids"]}
    if contract["kind"] == "full_archived_extraction" and set(truth) != set(all_truth):
        raise ValueError("Full-cohort scoring requires all frozen cases")
    original_rows = rows
    if reproject:
        rows = [
            {**r, "normalized_fields": normalize(r["method"], r.get("response", {}))} for r in rows
        ]
    scores = score_rows(home, truth, rows, contract["methods"])
    result = {
        "kind": contract["kind"],
        "cases": len(truth),
        "scores": scores,
        "call_manifest_sha256": digest((run / "manifest.json").read_bytes()),
        "scorer_sha256": digest((home / "dataset/evaluate.py").read_bytes()),
    }
    if reproject:
        result["projection_revision"] = {
            "schema": "native-field-projection/1",
            "root_cause": {
                method: (
                    "The upstream baseline emits a canonical stock indicator but can omit "
                    "availability; project only an unambiguous indicator"
                    if method == "extruct"
                    else "Native sold_out stock label was not mapped to OutOfStock"
                )
                for method in contract["methods"]
            },
            "regression_ids": list(PROJECTION_REGRESSIONS),
            "pipeline_sha256": digest(Path(__file__).read_bytes()),
            "original_scores_sha256": digest((run / "scores.json").read_bytes()),
            "changed_cases": [
                r["case_id"]
                for old, r in zip(original_rows, rows, strict=True)
                if old.get("normalized_fields") != r.get("normalized_fields")
            ],
            "provider_calls_added": 0,
        }
    target = Path(output) if output else run / "scores.json"
    if reproject and target.resolve() == (run / "scores.json").resolve():
        raise ValueError("A correction must preserve original scores")
    if target.exists():
        if read_json(target) != result:
            raise ValueError("Existing score differs; use an explicit correction")
    else:
        save_json(target, result, exclusive=True)
    return result


def score_rows(home, truth, rows, methods):
    """Use the same upstream scoring boundary for original and repaired views."""
    from decimal import InvalidOperation

    scorer = official_scorer(home)
    original_decimal = scorer["decimal_matching"]

    def price_match(gold, prediction):
        try:
            return original_decimal(gold, prediction)
        except InvalidOperation:
            return scorer["hard_matching"](gold, prediction)

    scores = {}
    for method in methods:
        predictions = {
            r["case_id"]: (r.get("normalized_fields") or {}) if r.get("http_status") == 200 else {}
            for r in rows
            if r["method"] == method
        }
        scores[method] = evaluate_predictions(
            truth, predictions, {**scorer, "decimal_matching": price_match}
        )
    return scores
