"""Pinned public inputs, immutable compatibility calls and upstream scoring."""

from __future__ import annotations

import ast
import base64
import copy
import fcntl
import gzip
import io
import json
import math
import multiprocessing
import os
import re
import signal
import tempfile
import time
import uuid
from concurrent.futures import CancelledError
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

from harness.benchmark_execution import ExecutionPolicy, Job, run_jobs
from harness.data_home import data_home as shared_data_home
from harness.distribution import repository_root
from harness.record_io import bytes_sha256 as digest
from harness.record_io import read_json as shared_read_json
from harness.web_extract.dataset import benchmark_config, input_mode
from harness.web_fetch.config import load_runtime_env
from harness.web_fetch.providers.http_client import request
from lexbench_webfetch.paths import validate_http_url

ROOT = repository_root()
PROFILE = Path(__file__).resolve().parents[1] / "benchmarks/cards/zyte-product-extraction.json"
PRODUCT_FIELDS = ("price", "sku", "availability")
FIELDS = (*PRODUCT_FIELDS, "InStock", "OutOfStock")
_RAW_SESSIONS = {}
_COLLECTION_CLOCK = None


def valid_product_value(field, value):
    """Validate native target values before normalization can discard bad types."""
    if field not in PRODUCT_FIELDS:
        raise ValueError("Unknown product target field: " + field)
    if value is None:
        return True
    if isinstance(value, (bool, dict, list)):
        return False
    if field == "price":
        try:
            return Decimal(str(value)).is_finite()
        except (InvalidOperation, ValueError):
            return False
    if field == "availability":
        return value in ("InStock", "OutOfStock")
    return isinstance(value, (str, int, float))


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


def validate_fixture_base(base):
    """Require a public path prefix that can be safely retained and extended."""
    validate_http_url(base)
    parsed = urlsplit(base)
    if parsed.scheme != "https":
        raise ValueError("Frozen fixture base requires HTTPS")
    if parsed.username is not None or parsed.password is not None or "?" in base or "#" in base:
        raise ValueError("Frozen fixture base must not contain credentials, query or fragment")
    return base


def fixture_url(base, case_id, suffix=""):
    validate_fixture_base(base)
    if suffix not in ("", ".html"):
        raise ValueError("Frozen fixture requires HTTPS and a declared path suffix")
    return f"{base.rstrip('/')}/{case_id}{suffix}"


def qualify_fixture(home, base, case_id, *, suffix="", timeout=60):
    url = fixture_url(base, case_id, suffix)
    frozen = (home / "dataset/html" / (case_id + ".html")).read_bytes()

    @total_deadline
    def fetch(*, timeout):
        response = request(
            url, headers={"Accept-Encoding": "gzip"}, timeout=timeout, max_bytes=10_000_000
        )
        if response.headers.get("content-encoding") == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(response.body)) as compressed:
                body = compressed.read(len(frozen) + 1)
        else:
            body = response.body
        return response, body

    for attempt in range(3):
        try:
            response, body = fetch(timeout=timeout)
            break
        except (TimeoutError, OSError):
            if attempt == 2:
                raise
            time.sleep(2)
    expected = digest(frozen)
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
    return shared_data_home() / "web-extract"


def prepare(destination: Path, profile=PROFILE, *, benchmark="zyte", source=None):
    adapter = benchmark_config(name=benchmark)["adapter"]
    if adapter:
        if source is None:
            raise ValueError("This benchmark requires its pinned source archive")
        return adapter.prepare(destination, Path(source))
    require_external_output(destination)
    contract = read_json(profile)
    pin = contract["dataset"]
    target = destination / "dataset"
    if (target / "manifest.json").exists():
        return validate_dataset(destination, profile)
    if target.exists():
        raise ValueError("Incomplete dataset exists; retain it and use a new destination")
    if pin.get("distribution"):
        from harness.web_extract.dataset import materialize

        materialize(destination, pin)
        return validate_dataset(destination, profile)
    raise ValueError("Dataset preparation requires a pinned Hugging Face distribution")


def selected_profile(home):
    """Resolve only registered dataset adapters; old Zyte homes retain their identity."""
    return benchmark_config(home)["profile"]


def validate_dataset(home: Path, profile=None):
    contract = read_json(profile or selected_profile(home))
    root = home / "dataset"
    manifest = read_json(root / "manifest.json")
    pin = contract["dataset"]
    inventory = digest(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    )
    if pin.get("inventory_sha256") and inventory != pin["inventory_sha256"]:
        from harness.web_extract.dataset import verify_distribution

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
        if not path.is_file():
            raise ValueError("Dataset bytes changed: " + name)
        raw = path.read_bytes()
        if digest(raw) != item["sha256"]:
            raise ValueError("Dataset bytes changed: " + name)
        if name.startswith("html/"):
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("Frozen HTML must be UTF-8: " + name) from error
    for name, expected in pin.get("files", {}).items():
        if manifest["files"].get(name, {}).get("sha256") != expected:
            raise ValueError("Pinned source binding changed")
    truth = read_json(root / "dataset/ground-truth.json")
    expected = {"html/" + key + ".html" for key in truth}
    if len(truth) != pin["cases"] or not expected.issubset(manifest["files"]):
        raise ValueError("Incomplete HTML population")
    if manifest.get("benchmark") == "records":
        from harness.web_extract.records import exported_evaluator

        if (root / "evaluate.py").read_bytes() != exported_evaluator():
            raise ValueError(
                "Pinned record evaluator differs from the runtime; use the original "
                "revision or a declared scoring correction"
            )
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
    if METHODS[method].get("deployment") == "self-hosted-open-source":
        identity = native_options.get("runtime_identity")
        if not native_options.get("endpoint") or not isinstance(identity, dict):
            raise ValueError("Self-hosted methods require an endpoint and frozen runtime identity")
        if any(
            not identity.get(key)
            for key in (
                "engine",
                "version",
                "model",
                "model_base",
                "implementation_sha256",
                "environment_sha256",
            )
        ):
            raise ValueError("Incomplete self-hosted runtime identity")
        if native_options.get("keepalive") is True and identity.get("json_keepalive") is not True:
            raise ValueError("Frozen runtime does not advertise JSON keepalive")
    if native_options.get("input_route", "html") not in ("html", "url"):
        raise ValueError("Native input route must be html or url")
    for key in (
        "setJavaScriptEnabled", "discussion", "use_fixture_context", "norender", "keepalive"
    ):
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




def _cloudflare_request(
    method,
    html,
    source_url,
    fixture_url,
    *,
    timeout,
    home,
    native_options,
    schema=None,
    prompt=None,
):
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/browser-rendering/json"
    headers = {"Authorization": "Bearer " + os.environ["CLOUDFLARE_API_TOKEN"]}
    body = {
        "html": html,
        "response_format": {"type": "json_schema", "json_schema": schema or SCHEMA},
    }
    body.update({k: v for k, v in native_options.items() if k != "input_route"})
    if native_options.get("input_route") == "url":
        if not fixture_url:
            raise ValueError("Cloudflare URL extraction requires a frozen fixture")
        del body["html"]
        body["url"] = fixture_url
    return url, headers, body


def _browserbase_request(
    method,
    html,
    source_url,
    fixture_url,
    *,
    timeout,
    home,
    native_options,
    schema=None,
    prompt=None,
):
    if not fixture_url:
        return {"state": "input_blocked", "error": "A frozen HTTPS snapshot URL is required"}
    url = "https://api.browserbase.com/v1/fetch"
    headers = {"X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"]}
    body = {
        "url": fixture_url,
        "format": "json",
        "schema": schema or SCHEMA,
        "proxies": False,
        "allowRedirects": True,
    }
    return url, headers, body


def _url_json_request(
    method,
    html,
    source_url,
    fixture_url,
    *,
    timeout,
    home,
    native_options,
    schema=None,
    prompt=None,
):
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
            "formats": [{"type": "json", "schema": schema or SCHEMA}],
            "onlyMainContent": False,
            "maxAge": 0,
        }
        if method == "firecrawl-json"
        else {
            "url": fixture_url,
            "output": ["json"],
            "json": {"schema": schema or SCHEMA},
            "run_js": False,
        }
    )
    return url, headers, body


def _firecrawl_parse_request(
    method,
    html,
    source_url,
    fixture_url,
    *,
    timeout,
    home,
    native_options,
    schema=None,
    prompt=None,
):
    """Upload the original HTML to Firecrawl's native file-to-JSON endpoint."""
    from urllib3.filepost import encode_multipart_formdata

    if not isinstance(schema, dict):
        raise ValueError("Firecrawl JSON parsing requires an explicit schema")
    options = {
        "formats": [{"type": "json", "schema": schema}],
        "onlyMainContent": False,
        "timeout": int(timeout * 1000),
    }
    if prompt:
        options["formats"][0]["prompt"] = prompt
    body, content_type = encode_multipart_formdata(
        [
            ("file", ("snapshot.html", html.encode("utf-8"), "text/html")),
            ("options", (None, json.dumps(options), "application/json")),
        ]
    )
    return (
        "https://api.firecrawl.dev/v2/parse",
        {
            "Authorization": "Bearer " + os.environ["FIRECRAWL_API_KEY"],
            "Content-Type": content_type,
        },
        body,
    )


def _zyte_request(
    method,
    html,
    source_url,
    fixture_url,
    *,
    timeout,
    home,
    native_options,
    schema=None,
    prompt=None,
):
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
    if method == "zyte-custom":
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("Zyte custom extraction requires an object schema")
        body = {
            "url": source_url,
            "pageContent": True,
            "pageContentOptions": {"extractFrom": "userHtml", "userHtml": html},
            "customAttributes": schema["properties"],
        }
        options = {k: v for k, v in native_options.items() if k != "input_route"}
        if options:
            body["customAttributesOptions"] = options
    if native_options.get("input_route") == "url":
        if not fixture_url:
            raise ValueError("Zyte URL extraction requires a frozen fixture")
        body["url"] = fixture_url
        key = "pageContentOptions" if method == "zyte-custom" else "productOptions"
        body[key] = {"extractFrom": "httpResponseBody"}
    return url, headers, body


def _scrapegraph_request(
    method,
    html,
    source_url,
    fixture_url,
    *,
    timeout,
    home,
    native_options,
    schema=None,
    prompt=None,
):
    url = "https://v2-api.scrapegraphai.com/api/extract"
    headers = {"SGAI-APIKEY": os.environ["SGAI_API_KEY"]}
    body = {
        "html": html,
        "prompt": prompt
        or (
            "Extract the main product current price, SKU and stock availability. "
            "Use null for absent fields."
        ),
        "schema": schema or SCHEMA,
    }
    if native_options.get("input_route") == "url":
        if not fixture_url:
            raise ValueError("ScrapeGraph URL extraction requires a frozen fixture")
        del body["html"]
        body["url"] = fixture_url
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
    url_input = native_options.get("input_route") == "url"
    if url_input:
        if not fixture_url:
            raise ValueError("Native URL extraction requires a frozen fixture")
        params["url"] = fixture_url
        if method == "scrapfly-product":
            endpoint = "https://api.scrapfly.io/scrape"
            params["render_js"] = "false"
    url = endpoint + "?" + urlencode(params)
    started = time.monotonic()
    session = _RAW_SESSIONS.get(method)
    if session is None:
        session = _RAW_SESSIONS[method] = requests.Session()
    session.cookies.clear()
    transport = session.get if url_input else session.post
    with transport(
        url,
        **({} if url_input else {"data": html.encode(), "headers": {"Content-Type": "text/html"}}),
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
                "input": "url" if url_input else "html",
                "html_sha256": digest(html.encode()),
                "source_url": source_url,
                "effective_context_url": params["url"],
                "fixture_url": fixture_url,
                "mode": "product",
                "cookie_policy": "no_inherited_api_cookies",
                "native_options": native_options,
            },
        }


def _self_hosted_request(
    method,
    html,
    source_url,
    fixture_url,
    *,
    timeout,
    home,
    native_options,
    schema=None,
    prompt=None,
):
    """Invoke a version-bound native extractor deployed by the caller."""
    if not fixture_url:
        raise ValueError("Self-hosted extraction requires a frozen snapshot URL")
    endpoint = native_options.get("endpoint")
    validate_http_url(endpoint)
    parsed = urlsplit(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Extraction endpoint must not contain credentials, query or fragment")
    identity = native_options.get("runtime_identity")
    engine = METHODS[method]["engine"]
    if not isinstance(identity, dict) or identity.get("engine") != engine:
        raise ValueError("Self-hosted extraction requires a matching frozen runtime identity")
    return (
        endpoint.rstrip("/") + "/extract/" + engine
        + ("?keepalive=true" if native_options.get("keepalive") else ""),
        {
            "Authorization": "Bearer " + os.environ["EXTRACTION_API_KEY"],
        },
        {
            "url": fixture_url,
            "schema": schema or SCHEMA,
            "instruction": prompt
            or (
                "Extract the fields defined by the schema from the main record."
                if schema
                else "Extract the main product current price, SKU and stock availability. "
                "Use null for absent fields."
            ),
            "expected_runtime": identity,
        },
    )


METHODS = {
    **{
        name: {
            "keys": ["EXTRACTION_API_KEY"],
            "input": "url",
            "custom_schema": True,
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": ["endpoint", "runtime_identity", "keepalive"],
            "adapter": _self_hosted_request,
            "deployment": "self-hosted-open-source",
            "engine": engine,
        }
        for name, engine in {
            "crawl4ai-json": "crawl4ai",
            "scrapegraph-oss-json": "scrapegraph",
            "stagehand-local-json": "stagehand",
            "firecrawl-oss-json": "firecrawl",
        }.items()
    },
    "firecrawl-parse-json": {
        "keys": ["FIRECRAWL_API_KEY"],
        "input": "html",
        "minimum_call_interval_seconds": 0,
        "post_call_cooldown_seconds": 0,
        "options": [],
        "custom_schema": True,
        "requires_custom_schema": True,
        "adapter": _firecrawl_parse_request,
    },
    "firecrawl-json": {
        "custom_schema": True,
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
        "custom_schema": True,
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
    "zyte-custom": {
        "keys": ["ZYTE_API_KEY"],
        "input": "html",
        "minimum_call_interval_seconds": 0,
        "post_call_cooldown_seconds": 0,
        "options": ["maxInputTokens", "maxOutputTokens", "input_route"],
        "custom_schema": True,
        "requires_custom_schema": True,
        "description_limit": 300,
        "adapter": _zyte_request,
    },
    "zyte-product": {
        **{
            "keys": ["ZYTE_API_KEY"],
            "input": "html",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": ["input_route"],
        },
        "adapter": _zyte_request,
    },
    "diffbot-product": {
        **{
            "keys": ["DIFFBOT_API_TOKEN"],
            "input": "html",
            "minimum_call_interval_seconds": 13,
            "post_call_cooldown_seconds": 10,
            "options": ["discussion", "use_fixture_context", "norender", "input_route"],
        },
        "adapter": _raw_html_request,
    },
    "scrapegraph-extract": {
        "custom_schema": True,
        "schema_prompt": "Extract the fields defined by the schema.",
        **{
            "keys": ["SGAI_API_KEY"],
            "input": "html",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": ["input_route"],
        },
        "adapter": _scrapegraph_request,
    },
    "scrapfly-product": {
        **{
            "keys": ["SCRAPFLY_API_KEY"],
            "input": "html",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": ["input_route"],
        },
        "adapter": _raw_html_request,
    },
    "oxylabs-json": {
        "custom_schema": True,
        **{
            "keys": ["OXYLABS_API_KEY"],
            "input": "url",
            "minimum_call_interval_seconds": 0,
            "post_call_cooldown_seconds": 0,
            "options": [],
        },
        "adapter": _url_json_request,
    },
    "browserbase-json": {
        "custom_schema": True,
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


RECORD_INSTRUCTION_DELIVERY = "schema-property-descriptions/1"

RECORD_INSTRUCTIONS = (
    "Extract all requested attributes of the main record. Copy text values "
    "exactly, including units and punctuation. Return an empty array for "
    "absent fields. Exclude navigation and other records."
)


def record_instructions(home):
    """Task scope belongs to the frozen dataset profile, with the original default."""
    prompt = read_json(selected_profile(home)).get("extraction_instructions", RECORD_INSTRUCTIONS)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Extraction instructions must be a nonempty string")
    return prompt


def validate_live_route(method, native_options=None):
    spec = available_methods()[method]
    if spec["input"] != "url" and (
        "input_route" not in spec.get("options", [])
        or (native_options or {}).get("input_route") != "url"
    ):
        raise ValueError("Live cohorts require a native URL extraction route: " + method)


def resolve_run_path(home, value):
    """Bare run IDs live under runs/; explicit relative paths start at the data home."""
    path = Path(value)
    if path.is_absolute():
        return path
    return Path(home) / (Path("runs") / path if len(path.parts) == 1 else path)


def instruction_schema(schema, prompt):
    """Put shared instructions in each field, including APIs that accept only properties."""
    if not prompt:
        return schema
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        raise ValueError("Extraction instructions require an object schema")
    result = copy.deepcopy(schema)
    for field in result["properties"].values():
        field["description"] = " ".join(filter(None, [field.get("description"), prompt]))
    return result


def validate_instruction_schema(method, schema):
    limit = METHODS[method].get("description_limit")
    if (
        schema is not None
        and limit is not None
        and any(
            len(field.get("description", "")) > limit for field in schema["properties"].values()
        )
    ):
        raise ValueError(f"{method} attribute descriptions exceed its {limit}-character limit")


@total_deadline
def provider_call(
    method,
    html,
    source_url,
    fixture_url=None,
    *,
    timeout=120,
    home=None,
    native_options=None,
    schema=None,
    prompt=None,
):
    """One native route per provider; no hidden fallback or quality retry."""
    native_options = validate_native_options(method, native_options)
    if schema is not None:
        if not METHODS[method].get("custom_schema", False):
            raise ValueError("Selected native interface does not support arbitrary schemas")
    schema = instruction_schema(schema, prompt)
    validate_instruction_schema(method, schema)
    prepared = METHODS[method]["adapter"](
        method,
        html,
        source_url,
        fixture_url,
        timeout=timeout,
        home=home,
        native_options=native_options,
        **(
            {
                "schema": schema,
                "prompt": METHODS[method].get("schema_prompt"),
            }
            if schema is not None
            else {}
        ),
    )
    if isinstance(prepared, dict):
        return prepared
    url, headers, body = prepared
    started = time.monotonic()
    response = request(
        url,
        method="POST",
        headers=headers,
        timeout=timeout,
        allow_redirects=False,
        **({"body": body} if isinstance(body, bytes) else {"json_body": body}),
    )
    payload = decoded_response(response.text())
    native_usage = {}
    if method == "firecrawl-parse-json" and isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("metadata"), dict):
            native_usage["parse_metadata"] = dict(data["metadata"])
    if (
        method == "zyte-custom"
        and isinstance(payload, dict)
        and isinstance(payload.get("customAttributes"), dict)
    ):
        metadata = payload["customAttributes"].get("metadata")
        if isinstance(metadata, dict):
            native_usage["custom_attributes"] = dict(metadata)
    return {
        "state": "response_received",
        "http_status": response.status,
        "elapsed_seconds": time.monotonic() - started,
        "response": redact(payload),
        "provider_usage": {
            **{
                k: v
                for k, v in response.headers.items()
                if k.lower() in {"x-browser-ms-used", "x-browser-time", "x-browser-usage"}
            },
            **native_usage,
        },
        "request": {
            "endpoint": url,
            "input": "url"
            if native_options.get("input_route") == "url"
            else available_methods()[method]["input"],
            "html_sha256": digest(html.encode()),
            "schema": schema or SCHEMA,
            "instructions": {"text": prompt, "location": "schema.properties.*.description"}
            if prompt
            else None,
            "standalone_prompt": METHODS[method].get("schema_prompt") if schema else None,
            "fixture_url": fixture_url,
            "source_url": source_url,
            "native_options": native_options,
            **({"follow_redirects": True} if method == "browserbase-json" else {}),
        },
    }


def normalize(method: str, payload: dict, *, schema=None, preserve_native_types=False):
    """Map exposed fields only. Never infer answers from labels or source URLs."""
    if not isinstance(payload, dict) or payload.get("success") is False:
        return None
    def mapping(value):
        return value if isinstance(value, dict) else {}

    if METHODS.get(method, {}).get("deployment") == "self-hosted-open-source":
        if payload.get("success") is not True:
            return None
        item = payload.get("data")
    elif method in ("firecrawl-json", "firecrawl-parse-json"):
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
            if not preserve_native_types and stock is not None and not isinstance(stock, str):
                stock = None
            item = {
                "price": price,
                "sku": mapping(p.get("identifiers")).get("sku"),
                "availability": {
                    "available": "InStock",
                    "unavailable": "OutOfStock",
                    "sold_out": "OutOfStock",
                }.get(stock, stock)
                if isinstance(stock, str)
                else stock,
            }
        else:
            item = None
    elif method == "zyte-custom":
        custom = mapping(payload.get("customAttributes"))
        if mapping(custom.get("metadata")).get("error"):
            return None
        item = custom.get("values")
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
    return normalize_extracted_fields(
        item, schema=schema, preserve_native_types=preserve_native_types
    )


def normalize_extracted_fields(item, *, schema=None, preserve_native_types=False):
    """Project an extracted object independently of its provider's response envelope."""
    if schema is not None or preserve_native_types:
        return dict(item) if isinstance(item, dict) else None
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


ACCESS_POLICY = {"receipt": "access-denial/1", "consecutive_403_sites": 3}
ACCESS_REASONS = {
    "service_capacity",
    "authentication",
    "permission",
    "quota_or_payment",
    "quota_or_permission",
    "credentials_missing",
    "input_configuration",
    "repeated_http_403",
}


def access_blocker(row):
    """Pause explicit account denials; ambiguous target denials need run-level evidence."""
    if row.get("state") == "input_blocked":
        return "input_configuration"
    if row.get("state") == "credentials_missing":
        return "credentials_missing"
    status, native = row.get("http_status"), row.get("response", {})
    failure = native.get("native_failure") if isinstance(native, dict) else None
    method = METHODS.get(row.get("method"), {})
    if (status == 429 and method.get("deployment") == "self-hosted-open-source"
            and isinstance(native, dict)
            and native.get("detail") == "Extraction worker capacity reached"):
        return "service_capacity"
    if (
        method.get("deployment") == "self-hosted-open-source"
        and isinstance(native, dict)
        and native.get("success") is False
        and isinstance(failure, dict)
        and failure.get("source") == "native-extractor-error"
        and failure.get("engine") == method.get("engine")
    ):
        nested = access_blocker({
            "http_status": failure.get("http_status"),
            "response": {"success": False, "error": failure.get("error", "")},
        })
        if nested:
            return nested
    if status == 401:
        return "authentication"
    if status == 402:
        return "quota_or_payment"
    native_error = isinstance(native, dict) and (
        native.get("success") is False or bool(native.get("error") or native.get("errors"))
    )
    if status not in (403, 429) and not native_error:
        return None
    message = (
        str(row.get("error", ""))
        + " "
        + (
            " ".join(
                str(native.get(k, ""))
                for k in ("error", "errors", "message", "title", "detail", "non_json_response")
            )
            if isinstance(native, dict)
            else ""
        )
    )
    if re.search(
        r"invalid (?:api[ _-]?)?key|invalid token|authentication failed|unauthenticated",
        message,
        re.I,
    ):
        return "authentication"
    subject = (
        r"\b(?:(?:(?:your|this|the)\s+)?"
        r"(?:account|plan|subscription|credentials?|api[ _-]?key|api[ _-]?token)"
        r"|(?:your|this|the)\s+(?:key|token)|api\s+(?:access|permissions?|entitlements?))\b"
    )
    denied = (
        r"(?:(?:is|are|was)\s+|has\s+been\s+)?"
        r"(?:not enabled|disabled|forbidden|not authorized|not allowed|not included|"
        r"permission denied|access denied)"
        r"|does not include|cannot\s+(?:fetch|access|use|call)"
    )
    if re.search(subject + r"\s+(?:" + denied + r")", message, re.I) or re.search(
        r"(?:permission denied|access denied|forbidden|not authorized)"
        r"(?:\s*:\s*|\s+(?:for|to)\s+)" + subject,
        message,
        re.I,
    ):
        return "permission"
    if re.search(
        r"quota|(?:insufficient|not enough|out of|exhausted|no remaining) credits?|"
        r"credits? (?:exhausted|depleted|limit)|account balance|insufficient balance|"
        r"credit balance (?:is )?too low|free plan|usage period|"
        r"subscription (?:expired|required|limit)|payment (?:required|failed)|account.*expired",
        message,
        re.I,
    ):
        return "quota_or_permission"
    return None


def retained_access_reason(row, contract):
    """Read the frozen dispatch decision, never today's classifier, for retained retries."""
    if "access_denial" in row:
        reason = row["access_denial"]
        if reason is not None and (not isinstance(reason, str) or reason not in ACCESS_REASONS):
            raise ValueError("Invalid retained access-denial reason")
        return reason
    if contract.get("access_policy") is not None:
        raise ValueError("Access-denial receipt is missing from a current-policy call")
    return legacy_access_reason(row)


def legacy_access_reason(row):
    """Frozen eligibility for historical calls without per-attempt stop receipts."""
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
    native = row.get("response", {})
    native_error = isinstance(native, dict) and (
        native.get("success") is False or bool(native.get("error"))
    )
    if status == 401:
        return "authentication"
    if status == 402:
        return "quota_or_payment"
    if status in (403, 429) or native_error:
        if re.search(
            r"invalid (?:api[ _-]?)?key|unauthorized|unauthenticated|"
            r"authentication failed|invalid token",
            message,
            re.I,
        ):
            return "authentication"
        if re.search(
            r"permission denied|access denied|not authorized|(?:api|account|token|key).*forbidden",
            message,
            re.I,
        ) and not re.search(r"target page|download|fetch|session", message, re.I):
            return "permission"
        if re.search(
            r"quota|(?:insufficient|not enough|out of|exhausted|no remaining) credits?|"
            r"credits? (?:exhausted|depleted|limit)|account balance|insufficient balance|"
            r"credit balance (?:is )?too low|free plan|usage period|"
            r"subscription (?:expired|required|limit)|"
            r"payment (?:required|failed)|account.*expired",
            message,
            re.I,
        ):
            return "quota_or_payment" if status == 402 else "quota_or_permission"
    return "legacy_http_403" if status == 403 else None


def require_external_output(path):
    path = Path(path)
    if path.is_symlink() or unsafe_parents(path):
        raise ValueError("Unsafe output destination")
    if path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Provider evidence must stay outside the source repository")


def probe(home: Path, methods: list[str], **options):
    """Run or resume one declared scope; never attach two workers to one run."""
    if options.get("fixture_base") is not None:
        validate_fixture_base(options["fixture_base"])
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
        "execution/benchmark_execution.py",
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


def _initialize_collection_clock(lock, clock, interval):
    global _COLLECTION_CLOCK
    _COLLECTION_CLOCK = lock, clock, interval


def _execute_collection_call(payload):
    (
        method,
        raw,
        source_url,
        public_url,
        schema_options,
        home,
        native_options,
        repair,
        custom_schema,
        row,
    ) = payload
    if custom_schema:
        from jsonschema import Draft202012Validator
    if _COLLECTION_CLOCK is not None:
        lock, clock, interval = _COLLECTION_CLOCK
        with lock:
            remaining = interval - (time.monotonic() - clock.value)
            if remaining > 0:
                time.sleep(remaining)
            clock.value = time.monotonic()
    try:
        last_call_started = time.monotonic()
        row["started_at"] = datetime.now(UTC).isoformat()
        row.update(
            provider_call(
                method,
                raw.decode("utf-8"),
                source_url,
                public_url,
                **schema_options,
                **(
                    {"home": home, "native_options": native_options, "timeout": 120}
                    if repair or native_options
                    else {}
                ),
            )
        )
        if "elapsed_seconds" not in row:
            row["elapsed_seconds"] = time.monotonic() - last_call_started
        normalized = normalize(
            method,
            row.get("response", {}),
            **({"schema": schema_options["schema"]} if custom_schema else {}),
        )
        row["normalized_fields"] = normalized
        if custom_schema:
            errors = list(Draft202012Validator(schema_options["schema"]).iter_errors(normalized))
            row["schema_valid"] = not errors
            row["schema_errors"] = [e.message for e in errors]
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
    row["finished_at"] = datetime.now(UTC).isoformat()
    return row


def _execute_admitted_call(value):
    admitted, payload = value
    return _execute_collection_call(payload) if admitted else payload


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
    execution_policy = contract.get("execution_policy")
    policy = ExecutionPolicy.from_dict(execution_policy) if execution_policy else None
    concurrency = policy.max_in_flight if policy else contract.get("concurrency", 1)
    if type(concurrency) is not int or not 1 <= concurrency <= 64:
        raise ValueError("Concurrency must be an integer between 1 and 64")
    if concurrency > 1 and policy and policy.backend != "process":
        raise ValueError("Native signal deadlines require the process execution backend")
    journal = run / "calls.journal.jsonl"
    if journal.is_symlink() or unsafe_parents(journal):
        raise ValueError("Symlink inputs are prohibited")
    registry = available_methods()
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    dataset_manifest = read_json(home / "dataset/manifest.json")
    frozen_inputs = dataset_manifest["files"]
    source_mode = input_mode(home)
    if contract.get("input_mode", "snapshot") != source_mode:
        raise ValueError("Collection input mode differs from the frozen dataset")
    if source_mode == "live":
        for method in methods:
            validate_live_route(method, native_options)
    adapter = benchmark_config(home)["adapter"]
    custom_schema = adapter is not None
    if custom_schema:
        from jsonschema import Draft202012Validator

        for schema in read_json(home / "dataset/schemas.json").values():
            Draft202012Validator.check_schema(schema)
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
        inventory = read_json(inflight)
        reservations = inventory.get("attempts", [inventory])
        for pending in reservations:
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
    denied_sites = {}

    def observe_denial(row):
        method = row["method"]
        if row.get("http_status") == 403 and row.get("compatibility") != "structured_output":
            site = urlsplit(truth[row["case_id"]]["url"]).hostname
            denied_sites.setdefault(method, set()).add(site)
        else:
            denied_sites.pop(method, None)
        return len(denied_sites.get(method, ())) >= ACCESS_POLICY["consecutive_403_sites"]

    for entry in physical:
        observe_denial(read_json(run / entry["path"]))
    records = []
    # Conservatively observe the full frozen pause after a cross-process resume.
    last_call_started = time.monotonic() if prior else None
    last_call_finished = last_call_started
    assignments = execution_policy.get("assignments") if execution_policy else None
    if assignments is None:
        if policy and len(methods) > 1 and set(policy.resources) != set(methods):
            raise ValueError("Multi-method shared resources require explicit assignments")
        assignments = {
            method: list(policy.resources) if policy and len(methods) == 1 else [method]
            for method in methods
        }
    elif (
        not isinstance(assignments, dict)
        or set(assignments) != set(methods)
        or any(
            not isinstance(resources, list)
            or method not in resources
            or any(not isinstance(resource, str) for resource in resources)
            or len(resources) != len(set(resources))
            for method, resources in assignments.items()
        )
        or {resource for resources in assignments.values() for resource in resources}
        != set(policy.resources)
    ):
        raise ValueError("Execution assignments must bind every method and declared resource")
    if concurrency > 1 and post_call_cooldown:
        raise ValueError("Post-call cooldown requires serial execution")
    pending_calls = {}
    blocked = []

    def save_pending():
        if pending_calls:
            values = list(pending_calls.values())
            value = values[0] if concurrency == 1 else {"attempts": values}
            save_json(inflight, value, exclusive=not inflight.exists())
        elif inflight.exists():
            inflight.unlink()

    jobs = []
    for ordinal, case_id in enumerate(case_ids):
        offset = ordinal % len(methods)
        for method in methods[offset:] + methods[:offset]:
            if (method, case_id) in prior:
                entry = prior[(method, case_id)]
                prior_row = read_json(run / entry["path"])
                if not (full and retained_access_reason(prior_row, contract)):
                    records.append(entry)
                    continue
            resources = tuple(assignments[method])
            jobs.append(Job(f"{method}/{case_id}", resources, (method, case_id)))

    def prepare(job):
        nonlocal last_call_started
        method, case_id = job.payload
        if dispatched_attempts.get((method, case_id), 0) > contract["access_retry_limit"]:
            raise PermissionError("Access remains blocked; declared retry budget exhausted")
        if len(physical) + len(pending_calls) >= contract.get(
            "maximum_physical_calls", float("inf")
        ):
            raise PermissionError("Frozen physical call budget exhausted before dispatch")
        missing = [key for key in registry[method]["keys"] if not os.getenv(key)]
        raw = (home / "dataset/html" / (case_id + ".html")).read_bytes()
        if digest(raw) != frozen_inputs["html/" + case_id + ".html"]["sha256"]:
            raise ValueError("Frozen extraction HTML changed before dispatch")
        schema_options = {}
        if custom_schema:
            schema_options = {
                "schema": adapter.task_schema(home, truth[case_id]),
                "prompt": contract.get("extraction_instructions", RECORD_INSTRUCTIONS),
            }
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
            pending_calls[(method, case_id)] = {
                "method": method,
                "case_id": case_id,
                "physical_attempt": attempts.get((method, case_id), 0) + 1,
                "input_sha256": digest(raw),
                "state": "dispatch_pending",
            }
            save_pending()
        last_call_started = time.monotonic()
        if missing:
            return (None, row)
        public_url = fixture_url(fixture_base, case_id, fixture_suffix) if fixture_base else None
        return (
            True,
            (
                method,
                raw,
                truth[case_id]["url"],
                public_url,
                schema_options,
                home,
                native_options,
                repair,
                custom_schema,
                row,
            ),
        )

    def commit(job, row, error):
        nonlocal last_call_finished
        if isinstance(error, CancelledError):
            pending_calls.pop(job.payload, None)
            save_pending()
            return
        if error is not None:
            raise error
        method, case_id = job.payload
        last_call_finished = time.monotonic()
        row.setdefault("finished_at", datetime.now(UTC).isoformat())
        row.setdefault("state", "response_received")
        number = attempts.get((method, case_id), 0) + 1
        row["physical_attempt"] = number
        if number > 1:
            prior_row = read_json(run / prior[(method, case_id)]["path"])
            row["retry_execution_seconds"] = prior_row.get("elapsed_seconds", 0) + prior_row.get(
                "retry_execution_seconds", 0
            )
        repeated_denial = observe_denial(row)
        row["access_denial"] = (
            (access_blocker(row) or ("repeated_http_403" if repeated_denial else None))
            if full
            else None
        )
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
        pending_calls.pop((method, case_id), None)
        save_pending()
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
        blocker = row["access_denial"]
        if blocker:
            save_json(
                run / "access-blocked.json",
                {
                    "method": method,
                    "case_id": case_id,
                    "reason": blocker,
                    **(
                        {"source_sites": sorted(denied_sites[method])}
                        if blocker == "repeated_http_403"
                        else {}
                    ),
                    "call": entry,
                    "remaining_logical_cases": len(case_ids) * len(methods) - len(records),
                },
            )
            action = (
                "inspect access-blocked.json before resuming the same run"
                if blocker == "repeated_http_403"
                else "repair access then resume the same run"
            )
            blocked.append(PermissionError(f"{method}: {blocker}; calls retained; {action}"))

    policy = policy or ExecutionPolicy.from_dict({
        "purpose": "quality",
        "max_in_flight": concurrency,
        "resources": {m: {"capacity": concurrency,
                          "reason": "Retain the declared provider pacing"} for m in methods},
        "backend": "process",
        "serial_reason": "Retain the declared serial call and cooldown policy",
    })
    context = multiprocessing.get_context("spawn")
    def ready_at(_job):
        return max(
            last_call_started + min_call_interval if last_call_started is not None else 0,
            last_call_finished + post_call_cooldown if last_call_finished is not None else 0,
        )
    run_jobs(
        jobs, _execute_admitted_call, commit, policy,
        prepare=prepare, should_stop=lambda: bool(blocked),
        ready_at=ready_at,
        initialize=_initialize_collection_clock,
        initargs=(context.Lock(), context.Value("d", time.monotonic() - min_call_interval),
                  min_call_interval),
    )
    if blocked:
        raise blocked[0]
    positions = {
        (m, cid): i
        for i, (cid, m) in enumerate(
            (cid, m)
            for i, cid in enumerate(case_ids)
            for m in methods[i % len(methods) :] + methods[: i % len(methods)]
        )
    }
    records.sort(
        key=lambda entry: positions[
            (read_json(run / entry["path"])["method"], read_json(run / entry["path"])["case_id"])
        ]
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
    native_options=None,
    maximum_physical_calls=None,
    snapshot_manifest=None,
    execution_policy=None,
    sample_case_ids=None,
):
    policy = ExecutionPolicy.from_dict(execution_policy) if execution_policy else None
    if policy and policy.max_in_flight > 1 and policy.backend != "process":
        raise ValueError("Native signal deadlines require the process execution backend")
    if policy and policy.max_in_flight > 1 and post_call_cooldown:
        raise ValueError("Post-call cooldown requires serial execution")
    if not isinstance(min_call_interval, (int, float)) or not 0 <= min_call_interval <= 3600:
        raise ValueError("Minimum call interval must be between 0 and 3600 seconds")
    if not isinstance(post_call_cooldown, (int, float)) or not 0 <= post_call_cooldown <= 3600:
        raise ValueError("Post-call cooldown must be between 0 and 3600 seconds")
    registry = available_methods()
    if not methods or len(set(methods)) != len(methods) or any(m not in registry for m in methods):
        raise ValueError("Select supported methods explicitly")
    if benchmark_config(home)["adapter"] is not None and any(
        not METHODS[method].get("custom_schema") for method in methods
    ):
        raise ValueError("Record datasets require methods supporting a custom schema")
    if benchmark_config(home)["adapter"] is None and any(
        METHODS[method].get("requires_custom_schema") for method in methods
    ):
        raise ValueError("Selected methods require a dataset-provided custom schema")
    validate_dataset(home)
    load_runtime_env()
    source_mode = input_mode(home)
    if source_mode == "live" and (fixture_base is not None or snapshot_manifest is not None):
        raise ValueError("A live cohort must use original source URLs, not hosted snapshots")
    if fixture_base is not None:
        validate_fixture_base(fixture_base)
    if fixture_suffix not in (".html", ""):
        raise ValueError("Frozen fixture suffix must be .html or empty")
    if native_options is not None:
        if len(methods) != 1:
            raise ValueError("Native controls require a single method run")
        native_options = validate_native_options(methods[0], native_options)
    if source_mode == "live":
        for method in methods:
            validate_live_route(method, native_options)
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
            else (
                benchmark_config(home)["adapter"].probe_cases(truth)
                if benchmark_config(home)["adapter"] is not None
                else probe_cases(truth)
            )
        )
    )
    if sample_case_ids is not None:
        if (
            not isinstance(sample_case_ids, list)
            or not sample_case_ids
            or len(set(sample_case_ids)) != len(sample_case_ids)
            or any(case_id not in truth for case_id in sample_case_ids)
        ):
            raise ValueError("Execution preflight requires distinct frozen sample cases")
        case_ids = sample_case_ids

    def verify_remote():
        def one(case_id):
            return qualify_fixture(home, fixture_base, case_id, suffix=fixture_suffix)

        if source_mode == "live":
            adapter = benchmark_config(home)["adapter"]
            if not hasattr(adapter, "qualify_live"):
                raise ValueError("The selected adapter cannot qualify live references")
            return adapter.qualify_live(home, case_ids)
        if not fixture_base:
            return []
        if snapshot_manifest is not None:
            from harness.web_extract.snapshots import verify_bound_manifest

            return verify_bound_manifest(
                home, snapshot_manifest, fixture_base, case_ids=case_ids, suffix=fixture_suffix
            )
        from harness.web_extract.snapshots import verify_resources

        return [one(case_id) for case_id in case_ids] + verify_resources(home, fixture_base)

    run = Path(run_dir)
    continuing = (run / "contract.json").exists()
    run.mkdir(parents=True, exist_ok=True)
    remote_before = verify_remote()
    requested = {
        "schema_version": 1,
        "kind": "execution_preflight"
        if sample_case_ids is not None
        else "full_archived_extraction"
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
        "access_retry_limit": 0 if sample_case_ids is not None else 1,
        "concurrency": policy.max_in_flight if policy else 1,
        "api_cookie_policy": "no_inherited_api_cookies",
        "access_policy": ACCESS_POLICY,
        "minimum_call_interval_seconds": min_call_interval,
        "post_call_cooldown_seconds": post_call_cooldown,
        "method_order": "rotate_by_case_ordinal",
        "fixture_base": fixture_base,
        "fixture_suffix": fixture_suffix,
        "dataset_manifest_sha256": digest((home / "dataset/manifest.json").read_bytes()),
        "pipeline_sha256": digest(Path(__file__).read_bytes()),
        "schema": read_json(home / "dataset/schemas.json")
        if benchmark_config(home)["adapter"] is not None
        else SCHEMA,
        "remote_fixture_before": remote_before,
    }
    if execution_policy is not None:
        requested["execution_policy"] = execution_policy
    if benchmark_config(home)["adapter"] is not None:
        from harness.web_extract.dataset import task_bindings

        requested.update(task_bindings(home))
        requested["extraction_instructions"] = record_instructions(home)
        requested["extraction_instruction_delivery"] = RECORD_INSTRUCTION_DELIVERY
    if benchmark_config(home)["adapter"] is not None or maximum_physical_calls is not None:
        matrix_size = len(case_ids) * len(methods)
        maximum = matrix_size + 20 if maximum_physical_calls is None else maximum_physical_calls
        if type(maximum) is not int or not matrix_size <= maximum <= matrix_size + 20:
            raise ValueError("Physical call budget must cover the cohort and at most 20 repairs")
        requested["maximum_physical_calls"] = maximum
    if snapshot_manifest is not None:
        requested["snapshot_manifest"] = snapshot_manifest
    if native_options is not None:
        requested["native_options"] = native_options
    scheduler_source = Path(__file__).parents[1] / "benchmark_execution.py"
    requested["execution_scheduler_sha256"] = digest(scheduler_source.read_bytes())
    requested["execution_scheduler_snapshot"] = "execution/benchmark_execution.py"
    if continuing:
        contract = read_json(run / "contract.json")
        if contract.get("snapshot_manifest") != requested.get("snapshot_manifest"):
            raise ValueError("Resume dataset snapshot manifest changed")
        if contract.get("input_mode") != requested.get("input_mode"):
            raise ValueError("Resume input mode changed")
        if contract.get("extraction_instructions") != requested.get("extraction_instructions"):
            raise ValueError("Resume extraction instructions changed")
        if contract.get("native_options") != requested.get("native_options"):
            raise ValueError("Resume native options changed")
        if contract.get("maximum_physical_calls") != requested.get("maximum_physical_calls"):
            raise ValueError("Resume physical call budget changed")
        if contract.get("dataset_registry_sha256") != requested.get("dataset_registry_sha256"):
            raise ValueError("Resume benchmark registry changed")
        if contract.get("task_adapter_sha256") != requested.get("task_adapter_sha256"):
            raise ValueError("Resume task adapter changed")
        if contract.get("task_scoring_dependencies_sha256") != requested.get(
            "task_scoring_dependencies_sha256"
        ):
            raise ValueError("Resume shared scoring implementation changed")
        if contract.get("dataset_profile_sha256") != requested.get("dataset_profile_sha256"):
            raise ValueError("Resume dataset profile changed")
        for field in (
            "kind",
            "case_ids",
            "methods",
            "concurrency",
            "minimum_call_interval_seconds",
            "post_call_cooldown_seconds",
            "fixture_base",
            "fixture_suffix",
            "dataset_manifest_sha256",
            "pipeline_sha256",
        ):
            if contract[field] != requested[field]:
                raise ValueError("Resume contract changed: " + field)
        if contract.get("execution_policy") != requested.get("execution_policy"):
            raise ValueError("Resume execution policy changed")
        if contract.get("execution_scheduler_sha256") != requested["execution_scheduler_sha256"]:
            raise ValueError("Resume execution scheduler changed; use the original source")
        verify_execution_snapshot(run, contract)
        if (run / "manifest.json").exists():
            load_run(run)
            return run
    else:
        contract = requested
        for source, relative in ((scheduler_source, contract["execution_scheduler_snapshot"]),):
            target = run / relative
            require_external_output(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = source.read_bytes()
            if target.exists():
                if target.read_bytes() != raw:
                    raise ValueError("Frozen execution source changed before initialization")
            else:
                with target.open("xb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
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
        native_options=native_options,
    )
    validate_dataset(home)
    receipt = verify_remote()
    after = run / "remote-fixture-after.json"
    if after.exists():
        old = read_json(after)

        def identity(items):
            return [
                (
                    r["case_id"],
                    r.get("reference_sha256", r.get("sha256")),
                    None if r.get("source_state") == "access_unavailable" else r.get("http_status"),
                    r.get("source_state"),
                    r.get("verification_mode"),
                    r.get("reference_observed_at"),
                )
                for r in items
            ]

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
    for field, identity in (
        ("pipeline_snapshot", "pipeline_sha256"),
        ("execution_scheduler_snapshot", "execution_scheduler_sha256"),
    ):
        relative = contract.get(field)
        if not relative:
            if identity == "execution_scheduler_sha256" and contract.get(identity):
                raise ValueError("Frozen execution scheduler snapshot is missing")
            continue
        path = Path(relative)
        snapshot = run / path
        if (
            path.is_absolute()
            or ".." in path.parts
            or snapshot.is_symlink()
            or not snapshot.is_file()
            or unsafe_parents(snapshot)
            or digest(snapshot.read_bytes()) != contract[identity]
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
        retained_access_reason(row, contract)
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
            if row.get("compatibility") == "structured_output" or not retained_access_reason(
                row, contract
            ):
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


RECORD_PROJECTION_REGRESSIONS = (
    "record-arrays-survive-native-reprojection",
    "invalid-record-values-remain-visible-to-the-scorer",
)


def check_record_projection_controls():
    for fields in (
        {"author": ["Ada"], "price": ["12.50"]},
        {"author": "invalid scalar", "price": True},
    ):
        if normalize("cloudflare-json", {"result": fields}, schema={"type": "object"}) != fields:
            raise ValueError("Record projection discarded or coerced native fields")
    return list(RECORD_PROJECTION_REGRESSIONS)


def score_run(
    home: Path,
    run: Path,
    *,
    reproject=False,
    output=None,
    reference_revision=None,
    verify_existing=False,
):
    """Score the declared population; retain empty predictions for failed calls."""
    require_external_output(Path(output) if output else run / "scores.json")
    if output and (
        Path(output).resolve().is_relative_to(ROOT.resolve())
        or Path(output).resolve().is_relative_to((run / "calls").resolve())
        or Path(output).resolve().is_relative_to((run / "execution").resolve())
    ):
        raise ValueError("Score revisions must stay outside Git and preserve physical evidence")
    if (reproject or reference_revision is not None) and output is None:
        raise ValueError("A projection or reference correction requires a separate output file")
    adapter = benchmark_config(home)["adapter"]
    if reproject:
        check_record_projection_controls() if adapter else check_projection_controls()
    contract, rows = load_run(run)
    validate_dataset(home)
    if contract["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Scoring input does not match call contract")
    all_truth = read_json(home / "dataset/dataset/ground-truth.json")
    original_truth = all_truth
    reference_policy = None
    if reference_revision is not None:
        if adapter is None:
            raise ValueError("Source reference revisions require a registered record adapter")
        all_truth, reference_policy = adapter.load_reference_revision(home, reference_revision)
    truth = {key: all_truth[key] for key in contract["case_ids"]}
    if contract["kind"] == "full_archived_extraction" and set(truth) != set(all_truth):
        raise ValueError("Full-cohort scoring requires all frozen cases")
    original_rows = rows
    if reproject:
        rows = [
            {
                **r,
                "normalized_fields": normalize(
                    r["method"],
                    r.get("response", {}),
                    schema=adapter.task_schema(home, truth[r["case_id"]]) if adapter else None,
                ),
            }
            for r in rows
        ]
    if reference_policy is not None:
        selected_groups = {
            (case["domain"], case["site"], field)
            for case in truth.values()
            for field in case["values"]
        }
        reference_policy = {
            **reference_policy,
            "unavailable_reference_groups": [
                group
                for group in reference_policy["unavailable_reference_groups"]
                if tuple(group) in selected_groups
            ],
        }
    scores = score_rows(home, truth, rows, contract["methods"], reference_policy=reference_policy)
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
                    "Record reprojection used product-only normalization; preserve the case's "
                    "native fields and invalid value types under its own task schema"
                    if adapter
                    else "Native sold_out stock label was not mapped to OutOfStock"
                )
                for method in contract["methods"]
            },
            "regression_ids": list(
                RECORD_PROJECTION_REGRESSIONS if adapter else PROJECTION_REGRESSIONS
            ),
            "pipeline_sha256": digest(Path(__file__).read_bytes()),
            "original_scores_sha256": digest((run / "scores.json").read_bytes()),
            "changed_cases": [
                r["case_id"]
                for old, r in zip(original_rows, rows, strict=True)
                if old.get("normalized_fields") != r.get("normalized_fields")
            ],
            "provider_calls_added": 0,
        }
    if reference_revision is not None:
        original = read_json(run / "scores.json")
        expected = score_rows(
            home,
            {cid: original_truth[cid] for cid in contract["case_ids"]},
            original_rows,
            contract["methods"],
        )
        if original.get("scores") != expected:
            raise ValueError("Original scores do not reproduce before reference correction")
        authority = read_json(reference_revision)
        result["reference_revision"] = {
            "schema": "source-qualified-record-scoring/1",
            "authority_sha256": digest(Path(reference_revision).read_bytes()),
            "original_reference_sha256": digest(
                (home / "dataset/dataset/ground-truth.json").read_bytes()
            ),
            "original_scores_sha256": digest((run / "scores.json").read_bytes()),
            "adapter_sha256": digest(Path(adapter.__file__).read_bytes()),
            "regression_ids": adapter.check_reference_controls(),
            "root_causes": [
                reason
                for observed, reason in (
                    (True, "Whitespace refinement preserves source characters, units and case"),
                    (
                        authority.get("dom_text_changes"),
                        "Encoded original labels differ from their exact frozen DOM text nodes",
                    ),
                    (
                        authority.get("partial_label_decisions"),
                        "Truncated original labels require full source elements and field context",
                    ),
                    (
                        authority.get("unavailable_reference_groups"),
                        "Entire original site-field annotations are absent, not quality zero",
                    ),
                )
                if observed
            ],
            "policy": reference_policy,
            "provider_calls_added": 0,
        }
    target = Path(output) if output else run / "scores.json"
    if (reproject or reference_revision is not None) and target.resolve() == (
        run / "scores.json"
    ).resolve():
        raise ValueError("A correction must preserve original scores")
    if verify_existing:
        if not reproject or not output or not target.is_file():
            raise ValueError("Projection verification requires an existing revision")
        # A frozen revision records the implementation that created it. Reproduce
        # its scores and correction scope without claiming today's code created it.
        previous = read_json(target)
        recorded_pipeline = previous.get("projection_revision", {}).get("pipeline_sha256")
        if not isinstance(recorded_pipeline, str) or not re.fullmatch(
            r"[0-9a-f]{64}", recorded_pipeline
        ):
            raise ValueError("Invalid recorded projection implementation")
        recorded_controls = previous.get("projection_revision", {}).get("regression_ids")
        if (
            not isinstance(recorded_controls, list)
            or not recorded_controls
            or any(not isinstance(value, str) or not value for value in recorded_controls)
            or len(set(recorded_controls)) != len(recorded_controls)
        ):
            raise ValueError("Invalid recorded projection regression controls")
        result["projection_revision"]["regression_ids"] = recorded_controls
        result["projection_revision"]["pipeline_sha256"] = recorded_pipeline
    if target.exists():
        if read_json(target) != result:
            raise ValueError("Existing score differs; use an explicit correction")
    else:
        save_json(target, result, exclusive=True)
    return result


def score_rows(home, truth, rows, methods, *, reference_policy=None):
    """Use the same upstream scoring boundary for original and repaired views."""
    from decimal import InvalidOperation

    if benchmark_config(home)["adapter"] is not None:
        score = benchmark_config(home)["adapter"].score

        return {
            method: score(
                truth,
                {
                    r["case_id"]: (r.get("normalized_fields") or {})
                    if r.get("http_status") == 200
                    else {}
                    for r in rows
                    if r["method"] == method
                },
                **(reference_policy or {}),
            )
            for method in methods
        }
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
