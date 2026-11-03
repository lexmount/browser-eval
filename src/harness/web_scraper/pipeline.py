"""Pinned public inputs, immutable compatibility calls and upstream scoring."""

from __future__ import annotations

import ast
import base64
import fcntl
import gzip
import io
import json
import os
import re
import runpy
import signal
import tarfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

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


def read_json(path: Path):
    if path.is_symlink() or unsafe_parents(path):
        raise ValueError("Symlink inputs are prohibited")
    return shared_read_json(path)


def unsafe_parents(path: Path):
    # macOS exposes the system temporary roots through these fixed OS aliases.
    aliases = {Path("/tmp"), Path("/var")} if os.uname().sysname == "Darwin" else set()
    return any(p.is_symlink() and p not in aliases for p in path.parents)


def save_json(path: Path, data, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def data_home() -> Path:
    return shared_data_home() / "web-scraper"


def prepare(destination: Path, profile=PROFILE):
    contract = read_json(profile)
    pin = contract["dataset"]
    target = destination / "dataset"
    if (target / "manifest.json").exists():
        return validate_dataset(destination, profile)
    if target.exists():
        raise ValueError("Incomplete dataset exists; retain it and use a new destination")
    url = f"https://codeload.github.com/{pin['repository']}/tar.gz/{pin['commit']}"
    response = request(url, timeout=120, max_bytes=40_000_000)
    if response.status != 200:
        raise ValueError(f"Dataset download failed: HTTP {response.status}")
    blobs = {}
    with tarfile.open(fileobj=io.BytesIO(response.body), mode="r:gz") as archive:
        for member in archive:
            parts = Path(member.name).parts[1:]
            if not parts:
                continue
            relative = Path(*parts)
            if relative.is_absolute() or ".." in relative.parts or member.issym() or member.islnk():
                raise ValueError("Unsafe upstream archive")
            if member.isfile() and (
                str(relative).startswith("dataset/")
                or str(relative) in {"evaluate.py", "README.rst", "LICENSE"}
            ):
                blobs[relative.as_posix()] = archive.extractfile(member).read()
    for name, expected in pin.get("files", {}).items():
        if name not in blobs or digest(blobs[name]) != expected:
            raise ValueError("Pinned upstream bytes changed: " + name)
    truth = json.loads(blobs["dataset/ground-truth.json"])
    if len(truth) != pin["cases"]:
        raise ValueError("Dataset population changed")
    for case_id in truth:
        source = "dataset/html/" + case_id + ".html.gz"
        alternatives = [n for n in blobs if n.startswith("dataset/html/" + case_id + ".")]
        if source not in blobs:
            if len(alternatives) != 1:
                raise ValueError("Missing or ambiguous HTML: " + case_id)
            source = alternatives[0]
        blobs["html/" + case_id + ".html"] = gzip.decompress(blobs[source])
    manifest = {
        "schema_version": 1,
        "dataset_id": pin["id"],
        "commit": pin["commit"],
        "archive_sha256": digest(response.body),
        "cases": len(truth),
        "files": {
            name: {"sha256": digest(body), "bytes": len(body)}
            for name, body in sorted(blobs.items())
        },
    }
    for name, body in blobs.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    save_json(target / "manifest.json", manifest, exclusive=True)
    return validate_dataset(destination, profile)


def validate_dataset(home: Path, profile=PROFILE):
    contract = read_json(profile)
    root = home / "dataset"
    manifest = read_json(root / "manifest.json")
    pin = contract["dataset"]
    inventory = digest(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    )
    if pin.get("inventory_sha256") and inventory != pin["inventory_sha256"]:
        raise ValueError("Pinned dataset inventory changed")
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


def official_scorer(home: Path):
    validate_dataset(home)
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


def score_predictions(truth: dict, predictions: dict, scorer, *, bootstrap=1000):
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
    scorer = official_scorer(home)
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    scores = {}
    for path in sorted((home / "dataset/dataset/output").glob("*.json")):
        scores[path.stem] = score_predictions(truth, read_json(path), scorer)
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
    return {
        "firecrawl-json": {"keys": ["FIRECRAWL_API_KEY"], "input": "url"},
        "cloudflare-json": {
            "keys": ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"],
            "input": "html",
        },
        "zyte-product": {"keys": ["ZYTE_API_KEY"], "input": "html"},
        "diffbot-product": {"keys": ["DIFFBOT_API_TOKEN"], "input": "html"},
        "scrapegraph-extract": {"keys": ["SGAI_API_KEY"], "input": "html"},
        "scrapfly-product": {"keys": ["SCRAPFLY_API_KEY"], "input": "html"},
        "oxylabs-json": {"keys": ["OXYLABS_API_KEY"], "input": "url"},
        "brightdata-scrapers": {"keys": ["BRIGHTDATA_API_TOKEN"], "input": "site_specific"},
        "extruct": {"keys": [], "input": "html"},
        "browserbase-json": {"keys": ["BROWSERBASE_API_KEY"], "input": "url"},
        "browserbase-stagehand": {
            "keys": ["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"],
            "input": "url",
        },
    }


def baseline_extractor(home: Path):
    """Execute the hash-pinned upstream baseline, without its CLI entry point."""
    pin = read_json(PROFILE)["baseline_extractor"]
    source = home / pin["path"]
    if not source.exists():
        response = request(pin["url"], timeout=60)
        if response.status != 200 or digest(response.body) != pin["sha256"]:
            raise ValueError("Baseline source does not match the pinned upstream extractor")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(response.body)
    if source.is_symlink() or digest(source.read_bytes()) != pin["sha256"]:
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
        for key, secret in os.environ.items():
            if (
                any(
                    x in key
                    for x in ("KEY", "TOKEN", "PASSWORD", "SECRET", "ACCOUNT_ID", "CDP_URL")
                )
                and len(secret) >= 8
            ):
                value = value.replace(secret, "[REDACTED]")
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


@total_deadline
def provider_call(method, html, source_url, fixture_url=None, *, timeout=120, home=None):
    """One native route per provider; no hidden fallback or quality retry."""
    if method == "extruct":
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
    if method == "browserbase-stagehand":
        if not fixture_url:
            return {"state": "input_blocked", "error": "A frozen HTTPS snapshot URL is required"}
        started = time.monotonic()
        from harness.web_scraper.stagehand import extract

        result = extract(fixture_url, SCHEMA, timeout=timeout)
        return {
            **redact(result),
            "elapsed_seconds": time.monotonic() - started,
            "request": {
                "input": "url",
                "endpoint": "stagehand:extract",
                "html_sha256": digest(html.encode()),
                "source_url": source_url,
                "fixture_url": fixture_url,
            },
        }
    if method == "cloudflare-json":
        url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/browser-rendering/json"
        headers = {"Authorization": "Bearer " + os.environ["CLOUDFLARE_API_TOKEN"]}
        body = {"html": html, "response_format": {"type": "json_schema", "json_schema": SCHEMA}}
    elif method == "browserbase-json":
        if not fixture_url:
            return {"state": "input_blocked", "error": "A frozen HTTPS snapshot URL is required"}
        url = "https://api.browserbase.com/v1/fetch"
        headers = {"X-BB-API-Key": os.environ["BROWSERBASE_API_KEY"]}
        body = {"url": fixture_url, "format": "json", "schema": SCHEMA, "proxies": False}
    elif method in ("firecrawl-json", "oxylabs-json"):
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
    elif method == "zyte-product":
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
    elif method == "scrapegraph-extract":
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
    elif method in ("diffbot-product", "scrapfly-product"):
        # These native uploaded-HTML routes use a raw body rather than JSON.
        from urllib.parse import urlencode

        import requests

        endpoint = (
            "https://api.diffbot.com/v3/product"
            if method == "diffbot-product"
            else "https://api.scrapfly.io/extraction"
        )
        params = (
            {"token": os.environ["DIFFBOT_API_TOKEN"], "url": source_url}
            if method == "diffbot-product"
            else {
                "key": os.environ["SCRAPFLY_API_KEY"],
                "url": source_url,
                "extraction_model": "product",
                "timeout": timeout,
            }
        )
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
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"non_json_response": raw.decode("utf-8", "replace")}
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
                    "mode": "product",
                },
            }
    else:
        raise ValueError("This method has no generic frozen-HTML route")
    started = time.monotonic()
    response = request(url, method="POST", headers=headers, json_body=body, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        payload = {"non_json_response": response.text()}
    return {
        "state": "response_received",
        "http_status": response.status,
        "elapsed_seconds": time.monotonic() - started,
        "response": redact(payload),
        "request": {
            "endpoint": url,
            "input": available_methods()[method]["input"],
            "html_sha256": digest(html.encode()),
            "schema": SCHEMA,
            "fixture_url": fixture_url,
            "source_url": source_url,
        },
    }


def normalize(method: str, payload: dict):
    """Map exposed fields only. Never infer answers from labels or source URLs."""
    if not isinstance(payload, dict) or payload.get("success") is False:
        return None
    if method == "extruct":
        item = payload.get("result")
        return (
            {f: str(item[f]) for f in FIELDS if item.get(f) is not None}
            if isinstance(item, dict)
            else None
        )
    if method == "firecrawl-json":
        item = payload.get("data", {}).get("json")
    elif method == "browserbase-json":
        status = payload.get("statusCode")
        if not isinstance(status, int) or not 200 <= status < 300:
            return None
        item = payload.get("content")
    elif method in ("cloudflare-json", "browserbase-stagehand"):
        item = payload.get("result")
    elif method == "oxylabs-json":
        results = payload.get("results") or []
        item = results[0].get("json") if len(results) == 1 else None
    elif method == "scrapegraph-extract":
        item = payload.get("json")
    elif method == "scrapfly-product":
        p = payload.get("data") or payload.get("result", {}).get("extracted_data", {}).get("data")
        if isinstance(p, dict):
            offers = p.get("offers") or []
            offer = offers[0] if offers else {}
            price = offer.get("price")
            if isinstance(price, dict):
                price = price.get("amount")
            stock = offer.get("availability")
            item = {
                "price": price,
                "sku": (p.get("identifiers") or {}).get("sku"),
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
        objects = payload.get("objects", [])
        p = objects[0] if len(objects) == 1 else None
        offer = p.get("offerPriceDetails", {}).get("amount", p.get("offerPrice")) if p else None
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
    if row.get("state") == "credentials_missing":
        return "credentials_missing"
    status = row.get("http_status")
    message = str(row.get("response", {})) + str(row.get("error", ""))
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
    if status == 429 or row.get("state") == "provider_error" or native_error:
        if re.search(
            r"quota|credits?|balance|free plan|usage period|upgrade|subscription|payment|"
            r"insufficient|expired",
            message,
            re.I,
        ):
            return "quota_or_permission"
    return None


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
    if run.is_symlink() or unsafe_parents(run):
        raise ValueError("Unsafe run directory")
    if run.resolve().is_relative_to(ROOT):
        raise ValueError("Provider evidence must stay outside the source repository")
    run.mkdir(parents=True, exist_ok=True)
    with (run / "execution.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _probe_unlocked(home, methods, run_dir=run, **options)


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
            requested_url = f"{fixture_base.rstrip('/')}/{case_id}{fixture_suffix}"
            for attempt in range(3):
                try:
                    response = request(
                        requested_url,
                        headers={"Accept-Encoding": "gzip"},
                        timeout=60,
                    )
                    break
                except (TimeoutError, OSError):
                    if attempt == 2:
                        raise
                    time.sleep(2)
            expected = digest((home / "dataset/html" / (case_id + ".html")).read_bytes())
            body = (
                gzip.decompress(response.body)
                if response.headers.get("content-encoding") == "gzip"
                else response.body
            )
            if response.status != 200 or digest(body) != expected:
                raise ValueError("Remote fixture differs from frozen HTML: " + case_id)
            if response.url != requested_url:
                raise ValueError("Use the canonical frozen fixture URL without redirects")
            return {
                "case_id": case_id,
                "sha256": expected,
                "http_status": response.status,
                "url": requested_url,
                "qualification_fetch_attempts": attempt + 1,
            }

        if not fixture_base:
            return []
        with ThreadPoolExecutor(max_workers=4) as executor:
            return list(executor.map(one, case_ids))

    run = (
        Path(run_dir)
        if run_dir
        else (
            home
            / "runs"
            / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
        )
    )
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
        "pipeline_snapshot": "execution/pipeline.py",
        "stagehand_sha256": digest(Path(__file__).with_name("stagehand.py").read_bytes())
        if "browserbase-stagehand" in methods
        else None,
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
            "stagehand_sha256",
            "baseline_extractor",
        ):
            if contract[field] != requested[field]:
                raise ValueError("Resume contract changed: " + field)
        if (run / "manifest.json").exists():
            load_run(run)
            return run
    else:
        contract = requested
        (run / "execution").mkdir(exist_ok=True)
        snapshot = run / "execution/pipeline.py"
        if snapshot.exists() and digest(snapshot.read_bytes()) != requested["pipeline_sha256"]:
            raise ValueError("Pre-dispatch pipeline snapshot changed")
        snapshot.write_bytes(Path(__file__).read_bytes())
        if "browserbase-stagehand" in methods:
            stagehand_source = Path(__file__).with_name("stagehand.py")
            snapshot = run / "execution/stagehand.py"
            if snapshot.exists() and digest(snapshot.read_bytes()) != requested["stagehand_sha256"]:
                raise ValueError("Pre-dispatch adapter snapshot changed")
            snapshot.write_bytes(stagehand_source.read_bytes())
            contract["stagehand_sha256"] = digest(stagehand_source.read_bytes())
        save_json(run / "contract.json", contract, exclusive=True)
    journal = run / "calls.journal.jsonl"
    prior = {}
    physical = []
    attempts = {}
    if journal.exists():
        for line in journal.read_text().splitlines():
            entry = json.loads(line)
            path = run / entry["path"]
            if digest(path.read_bytes()) != entry["sha256"]:
                raise ValueError("Checkpoint call changed")
            record = read_json(path)
            key = (record["method"], record["case_id"])
            prior[key] = entry
            physical.append(entry)
            attempts[key] = attempts.get(key, 0) + 1
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
                "Ambiguous in-flight attempt; reconcile retained evidence before resuming"
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
                if attempts[(method, case_id)] > contract["access_retry_limit"]:
                    raise PermissionError("Access remains blocked; declared retry budget exhausted")
            missing = [key for key in registry[method]["keys"] if not os.getenv(key)]
            raw = (home / "dataset/html" / (case_id + ".html")).read_bytes()
            row = {
                "method": method,
                "case_id": case_id,
                "input_sha256": digest(raw),
                "kind": "structured_extraction_attempt" if full else "compatibility_probe",
            }
            if missing:
                row.update(state="credentials_missing", missing_variables=missing)
            elif registry[method]["input"] == "site_specific":
                row.update(
                    state="input_blocked",
                    error="Site-specific scraper cannot be substituted for a generic HTML parser",
                )
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
                descriptor = os.open(run, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                try:
                    last_call_started = time.monotonic()
                    row.update(
                        provider_call(
                            method,
                            raw.decode("utf-8"),
                            truth[case_id]["url"],
                            f"{fixture_base.rstrip('/')}/{case_id}{fixture_suffix}"
                            if fixture_base
                            else None,
                            **({"home": home} if method == "extruct" else {}),
                        )
                    )
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
                    )
                last_call_finished = time.monotonic()
            number = attempts.get((method, case_id), 0) + 1
            row["physical_attempt"] = number
            if number > 1:
                prior_row = read_json(run / prior[(method, case_id)]["path"])
                row["retry_execution_seconds"] = prior_row.get(
                    "elapsed_seconds", 0
                ) + prior_row.get("retry_execution_seconds", 0)
            suffix = "" if number == 1 else f"-attempt{number}"
            path = run / "calls" / f"{method}-{case_id}{suffix}.json"
            save_json(path, redact(row), exclusive=True)
            entry = {"path": path.relative_to(run).as_posix(), "sha256": digest(path.read_bytes())}
            with journal.open("a") as checkpoint:
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


def load_run(run: Path):
    manifest = read_json(run / "manifest.json")
    contract = read_json(run / "contract.json")
    if contract.get("pipeline_snapshot"):
        snapshot = run / contract["pipeline_snapshot"]
        if (
            Path(contract["pipeline_snapshot"]).is_absolute()
            or ".." in Path(contract["pipeline_snapshot"]).parts
            or snapshot.is_symlink()
            or digest(snapshot.read_bytes()) != contract["pipeline_sha256"]
        ):
            raise ValueError("Frozen execution source changed")
    if digest((run / "contract.json").read_bytes()) != manifest["contract_sha256"]:
        raise ValueError("Call contract changed")
    if (
        contract.get("stagehand_sha256")
        and digest((run / "execution/stagehand.py").read_bytes()) != contract["stagehand_sha256"]
    ):
        raise ValueError("Frozen Stagehand client changed")
    for entry in manifest.get("physical_calls", []):
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe physical evidence path")
        path = run / relative
        if path.is_symlink() or digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Physical call evidence changed")
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


def score_run(home: Path, run: Path, *, reproject=False, output=None):
    """Score the declared population; retain empty predictions for failed calls."""
    from decimal import InvalidOperation

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
    scorer = official_scorer(home)
    original_decimal = scorer["decimal_matching"]

    def price_match(gold, prediction):
        try:
            return original_decimal(gold, prediction)
        except InvalidOperation:
            # A non-numeric emitted value is a false positive, not an omitted value.
            return scorer["hard_matching"](gold, prediction)

    scores = {}
    for method in contract["methods"]:
        predictions = {
            r["case_id"]: (r.get("normalized_fields") or {}) if r.get("http_status") == 200 else {}
            for r in rows
            if r["method"] == method
        }
        per_field = {}
        for field in FIELDS:
            matcher = price_match if field == "price" else scorer["hard_matching"]
            per_field[field] = scorer["evaluate"](
                truth, predictions, field, matcher, n_bootstrap=1000, show_errors=False
            )
        scores[method] = per_field
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
            "root_cause": "Native sold_out stock label was not mapped to OutOfStock",
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
