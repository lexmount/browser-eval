"""Native open-source extraction workers and a small authenticated HTTP boundary."""

import asyncio
import contextlib
import fcntl
import hashlib
import hmac
import importlib.metadata
import json
import os
import resource
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from harness.benchmark_execution import validate_capacity
from harness.record_io import optional_file_sha256

ENGINES = {
    "crawl4ai": "crawl4ai",
    "scrapegraph": "scrapegraphai",
    "stagehand": "stagehand",
    "firecrawl": None,
}
KEEPALIVE_SECONDS = 5
HOST_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
ADMISSION_DIRECTORY = Path(tempfile.gettempdir()) / "browser-eval-extraction-admission"


def worker_capacity():
    return validate_capacity(int(os.environ.get("EXTRACTION_MAX_WORKERS", "1")))


def acquire_worker_slot(capacity):
    """Share one container budget across requests and ASGI worker processes."""
    if ADMISSION_DIRECTORY.is_symlink():
        raise ValueError("Extraction admission directory must not be a symlink")
    ADMISSION_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    for number in range(capacity):
        descriptor = os.open(ADMISSION_DIRECTORY / str(number),
                             os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        lease = os.fdopen(descriptor, "a", encoding="utf-8")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lease
        except BlockingIOError:
            lease.close()
    return None


def validate_input(value):
    """Restrict collection to the declared immutable snapshot origins."""
    from jsonschema import SchemaError, validators

    if not isinstance(value, dict) or not isinstance(value.get("url"), str):
        raise ValueError("Request must be an object with a string URL")
    parsed = urlsplit(value["url"])
    origins = {
        s.strip().rstrip("/")
        for s in os.environ.get("SNAPSHOT_ORIGINS", "").split(",")
        if s.strip()
    }
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if parsed.scheme != "https" or parsed.username or parsed.password or origin not in origins:
        raise ValueError("URL must use a configured HTTPS snapshot origin")
    schema = value.get("schema")
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or not isinstance(schema.get("properties"), dict)
        or not schema["properties"]
    ):
        raise ValueError("An object JSON Schema with properties is required")
    try:
        validators.validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Invalid JSON Schema") from exc
    if "instruction" in value and not isinstance(value["instruction"], str):
        raise ValueError("Instruction must be a string")
    if value.get("engine") not in ENGINES:
        raise ValueError("Unknown extraction engine")
    if value["engine"] == "firecrawl" and not os.environ.get("FIRECRAWL_BACKEND_URL"):
        raise ValueError("Firecrawl backend is not configured")
    if value.get("expected_runtime") != runtime_identity(value["engine"]):
        raise ValueError("Runtime differs from the frozen extraction configuration")
    return value


_ENVIRONMENT_FILE_HASHES = {}
_ENVIRONMENT_CACHE_RECEIPT = None


def installed_environment_sha256():
    """Bind installed source, native libraries, package data and bytecode."""
    global _ENVIRONMENT_FILE_HASHES
    roots = sorted({Path(sysconfig.get_paths()[key]).resolve() for key in ("purelib", "platlib")})
    observed = {}
    result = hashlib.sha256()
    for index, root in enumerate(roots):
        prefix_length = len(root.as_posix().rstrip("/") + "/")
        for directory, directories, names in os.walk(root):
            directories[:] = sorted(directories)
            for name in (*directories, *sorted(names)):
                path = Path(directory) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError("Installed extraction environment contains a symlink")
                if stat.S_ISDIR(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Installed extraction environment requires regular files")
                signature = (info.st_dev, info.st_ino, info.st_size,
                             info.st_mtime_ns, info.st_ctime_ns)
                cached = _ENVIRONMENT_FILE_HASHES.get(path)
                if os.name != "nt" and cached and cached[0] == signature:
                    checksum = cached[1]
                else:
                    with path.open("rb") as stream:
                        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                    after = path.stat()
                    if signature != (after.st_dev, after.st_ino, after.st_size,
                                     after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError("Installed extraction environment changed during hashing")
                observed[path] = (signature, checksum)
                result.update(json.dumps((index, path.as_posix()[prefix_length:], checksum))
                              .encode("utf-8") + b"\n")
    _ENVIRONMENT_FILE_HASHES = observed
    return result.hexdigest()


@lru_cache(maxsize=4)
def browser_version(executable, _file_identity):
    """Avoid a second version subprocess for the same native browser file."""
    return subprocess.check_output([executable, "--version"], text=True, timeout=5).strip()


def host_boot_sha256():
    """Identify a controlled Linux host generation without exposing its raw boot ID."""
    return optional_file_sha256(HOST_BOOT_ID_PATH)


@lru_cache(maxsize=len(ENGINES))
def local_runtime_identity(engine):
    base = urlsplit(os.environ["EXTRACTION_MODEL_BASE"])
    if (
        base.scheme not in ("http", "https")
        or not base.netloc
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
    ):
        raise ValueError("Model base URL must not contain credentials, query or fragment")
    identity = {
        "engine": engine,
        "version": importlib.metadata.version(ENGINES[engine])
        if ENGINES[engine]
        else os.environ["FIRECRAWL_IMAGE"],
        "model": os.environ["EXTRACTION_MODEL"],
        "model_base": os.environ["EXTRACTION_MODEL_BASE"],
        "model_context_tokens": int(os.environ.get("EXTRACTION_MODEL_CONTEXT_TOKENS", "128000")),
        "implementation": "native-open-source/1",
        "json_keepalive": True,
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment_sha256": (_ENVIRONMENT_CACHE_RECEIPT or {}).get("environment_sha256")
        or installed_environment_sha256(),
        "environment_binding": "installed-files/1",
        "bytecode_cache": "precompiled-installed-files",
        "host_boot_sha256": host_boot_sha256(),
        "service_worker_capacity": worker_capacity(),
        "native_thread_limits": {
            key: os.environ.get(key) for key in (
                "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"
            )
        },
        "container_limits": {
            name: path.read_text(encoding="utf-8").strip() if path.is_file() else None
            for name, path in (("cpu", Path("/sys/fs/cgroup/cpu.max")),
                               ("memory", Path("/sys/fs/cgroup/memory.max")))
        },
    }
    if engine == "scrapegraph":
        identity["native_input"] = "html"
    if engine == "crawl4ai":
        identity["llm_output_protocol"] = "xml_blocks"
    if engine == "stagehand":
        executable = Path(os.environ["CHROME_PATH"]).resolve()
        with executable.open("rb") as stream:
            if stream.read(2) == b"#!":
                raise ValueError("CHROME_PATH must name the native browser, not a launcher script")
        with executable.open("rb") as stream:
            identity["browser_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        identity_stat = executable.stat()
        identity["browser_version"] = browser_version(
            str(executable),
            (identity_stat.st_ino, identity_stat.st_size, identity_stat.st_mtime_ns)
        )
    return identity


def runtime_identity(engine):
    identity = dict(local_runtime_identity(engine))
    if engine == "firecrawl":
        import requests

        endpoint = urlsplit(os.environ["FIRECRAWL_BACKEND_URL"])
        attestation = urlsplit(os.environ["FIRECRAWL_IDENTITY_URL"])
        if (
            any(url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.password is not None or url.query or url.fragment
                for url in (endpoint, attestation))
            or endpoint.scheme != attestation.scheme or endpoint.hostname != attestation.hostname
            or endpoint.path not in ("", "/") or attestation.path != "/identity"
        ):
            raise ValueError("Firecrawl backend endpoint and identity differ")
        response = requests.get(
            os.environ["FIRECRAWL_IDENTITY_URL"],
            timeout=5,
            allow_redirects=False,
        )
        response.raise_for_status()
        backend = response.json()
        if not isinstance(backend, dict) or backend.get("schema") != "firecrawl-runtime/2":
            raise ValueError("Incomplete Firecrawl backend attestation")
        if any(
            not isinstance(backend.get(key), str) or len(backend[key]) != 64
            for key in (
                "native_bundle_sha256",
                "native_dependencies_sha256",
                "node_sha256",
                "wrapper_sha256",
            )
        ):
            raise ValueError("Incomplete Firecrawl backend attestation")
        controls = backend.get("controls", {})
        if not isinstance(controls, dict):
            raise ValueError("Incomplete Firecrawl backend controls")
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
        if backend.get("kind") != "extractor" or str(controls.get("PORT") or "3002") != str(port):
            raise ValueError("Firecrawl backend endpoint kind or port differs")
        if controls.get("PLAYWRIGHT_MICROSERVICE_URL"):
            browser = backend.get("browser")
            if not isinstance(browser, dict) or browser.get("schema") != "firecrawl-runtime/2":
                raise ValueError("Incomplete Firecrawl browser attestation")
        if controls.get("MODEL_NAME") != identity["model"] or controls.get(
            "OPENAI_BASE_URL", ""
        ).rstrip("/") != identity["model_base"].rstrip("/"):
            raise ValueError("Firecrawl backend model differs from the declared configuration")
        identity["backend"] = backend
        identity["backend_url"] = os.environ["FIRECRAWL_BACKEND_URL"]
        identity["identity_url"] = os.environ["FIRECRAWL_IDENTITY_URL"]
    return identity


async def crawl(value):
    from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, LLMConfig
    from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
    from crawl4ai.extraction_strategy import LLMExtractionStrategy

    strategy = LLMExtractionStrategy(
        llm_config=LLMConfig(
            provider="openai/" + os.environ["EXTRACTION_MODEL"],
            api_token=os.environ["EXTRACTION_MODEL_API_KEY"],
            base_url=os.environ["EXTRACTION_MODEL_BASE"],
            backoff_max_attempts=1,
        ),
        schema=value["schema"],
        instruction=value.get("instruction", "Extract the requested fields of the main record."),
        extraction_type="schema",
        apply_chunking=False,
        input_format="markdown",
        force_json_response=False,
        extra_args={"temperature": 0, "max_tokens": 8192},
    )
    async with AsyncWebCrawler(crawler_strategy=AsyncHTTPCrawlerStrategy()) as crawler:
        result = await crawler.arun(
            value["url"],
            config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS, extraction_strategy=strategy),
        )
    if not result.success:
        return None, {"error": result.error_message, "extracted_content": result.extracted_content}
    try:
        native = json.loads(result.extracted_content)
    except (ValueError, TypeError) as exc:
        return None, {"error": str(exc), "extracted_content": result.extracted_content}
    # The task asks for one record. Never choose the best of multiple candidates.
    item = native[0] if isinstance(native, list) and len(native) == 1 else native
    if not isinstance(item, dict) or item.get("error"):
        return None, {"extracted_content": native, "usage": vars(strategy.total_usage)}
    # Crawl4AI 0.9.5 overwrites its own error flag with False. Its tagged error
    # envelope still identifies the failure; retain records with real fields.
    if item.get("tags") == ["error"] and set(item) <= {"index", "error", "tags", "content"}:
        return None, {"extracted_content": native, "usage": vars(strategy.total_usage)}
    data = {k: v for k, v in item.items() if k in value["schema"]["properties"]}
    return data, {"extracted_content": native, "usage": vars(strategy.total_usage)}


def graph(value):
    import requests
    from json_schema_to_pydantic import create_model
    from langchain_openai import ChatOpenAI
    from scrapegraphai.graphs import SmartScraperGraph

    model = ChatOpenAI(
        model=os.environ["EXTRACTION_MODEL"],
        api_key=os.environ["EXTRACTION_MODEL_API_KEY"],
        base_url=os.environ["EXTRACTION_MODEL_BASE"],
        temperature=0,
        max_tokens=8192,
        max_retries=0,
        timeout=100,
    )
    response = requests.get(
        value["url"],
        headers={"User-Agent": "Mozilla/5.0 BrowserEval-Snapshot/1.0"},
        timeout=30,
        allow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"Snapshot returned HTTP {response.status_code}")
    extractor = SmartScraperGraph(
        prompt=value.get("instruction", "Extract the requested fields of the main record."),
        source=response.content.decode("utf-8"),
        schema=create_model(value["schema"]),
        config={
            "llm": {
                "model_instance": model,
                "model_tokens": int(os.environ.get("EXTRACTION_MODEL_CONTEXT_TOKENS", "128000")),
            },
            "verbose": False,
            "headless": True,
            "reattempt": False,
            "loader_kwargs": {
                "timeout": 30,
                **(
                    {"executable_path": os.environ["CHROME_PATH"]}
                    if os.environ.get("CHROME_PATH")
                    else {}
                ),
            },
        },
    )
    result = extractor.run()
    return result, {
        "answer": result,
        "execution": extractor.get_execution_info(),
        "input_html_sha256": hashlib.sha256(response.content).hexdigest(),
    }


async def stagehand_extract(value):
    phase_started = time.monotonic()
    from json_schema_to_pydantic import create_model
    from openai import AsyncOpenAI
    from stagehand import (
        LLMStructuredGenerateParams,
        LLMStructuredGenerateResult,
        Stagehand,
        local_browser,
    )

    timings = {"sdk_import_seconds": time.monotonic() - phase_started}
    calls = []
    async with AsyncOpenAI(
        api_key=os.environ["EXTRACTION_MODEL_API_KEY"],
        base_url=os.environ["EXTRACTION_MODEL_BASE"],
        max_retries=0,
        timeout=100,
    ) as client:

        async def generate(params):
            if not isinstance(params, LLMStructuredGenerateParams):
                raise TypeError("Extraction requires native structured generation")
            schema = params.response_format.schema_.model_dump(mode="json")
            messages = [
                {
                    "role": "system",
                    "content": (params.system_prompt or "")
                    + "\nReturn JSON matching this schema:\n"
                    + json.dumps(schema),
                }
            ]
            for message in params.messages:
                content = message.model_dump(mode="json")["content"]
                blocks = content if isinstance(content, list) else [content]
                if any(b.get("type") != "text" for b in blocks):
                    raise TypeError("This extraction configuration supports text only")
                messages.append(
                    {"role": message.role.value, "content": "\n".join(b["text"] for b in blocks)}
                )
            response = await client.chat.completions.create(
                model=os.environ["EXTRACTION_MODEL"],
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=8192,
                    )
            calls.append(response.model_dump(mode="json"))
            text = response.choices[0].message.content
            return LLMStructuredGenerateResult.model_validate(
                {
                    "role": "assistant",
                    "content": {"type": "text", "text": text},
                    "output_format": "json_schema",
                    "structured_content": json.loads(text),
                    "stop_reason": response.choices[0].finish_reason,
                    "usage": {
                        "input_tokens": response.usage.prompt_tokens,
                        "output_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    }
                    if response.usage
                    else None,
                }
            )

        phase_started = time.monotonic()
        browser = await local_browser.launch(
            headless=True,
            chromium_sandbox=False,
            **(
                {"executable_path": os.environ["CHROME_PATH"]}
                if os.environ.get("CHROME_PATH")
                else {}
            ),
        )
        timings["browser_launch_seconds"] = time.monotonic() - phase_started
        try:
            phase_started = time.monotonic()
            extractor = await Stagehand.create(browser=browser, model=generate, cache=False)
            timings["session_setup_seconds"] = time.monotonic() - phase_started
            try:
                page = (await browser.context.pages())[0]
                phase_started = time.monotonic()
                await page.goto(value["url"])
                timings["navigation_seconds"] = time.monotonic() - phase_started
                phase_started = time.monotonic()
                result = await extractor.extract(
                    value.get("instruction", "Extract the fields of the main record."),
                    create_model(value["schema"]),
                )
                timings["extraction_seconds"] = time.monotonic() - phase_started
                return result.data.model_dump(mode="json"), {
                    "extract": result.model_dump(mode="json"),
                    "model_calls": calls,
                    "timings": timings,
                }
            finally:
                await extractor.close()
        finally:
            await browser.close()


def firecrawl_extract(value):
    import requests

    response = requests.post(
        os.environ["FIRECRAWL_BACKEND_URL"].rstrip("/") + "/v2/scrape",
        json={
            "url": value["url"],
            "formats": [
                {
                    "type": "json",
                    "schema": value["schema"],
                    "prompt": value.get("instruction", "Extract the fields defined by the schema."),
                }
            ],
            "onlyMainContent": False,
            "maxAge": 0,
            "timeout": 100000,
        },
        timeout=105,
        allow_redirects=False,
    )
    try:
        native = response.json()
    except ValueError:
        return None, {"http_status": response.status_code, "response_text": response.text}
    if response.status_code != 200 or native.get("success") is not True:
        return None, {"http_status": response.status_code, "response": native}
    return native.get("data", {}).get("json"), native


def native_failure(engine, native):
    """Expose machine error channels only, never arbitrary extracted page text."""
    if not isinstance(native, dict):
        return None
    status, errors = None, []
    if engine == "firecrawl":
        status = native.get("http_status")
        body = native.get("response", {})
        if isinstance(body, dict):
            errors.extend(body.get(key) for key in ("error", "errors", "message") if body.get(key))
    elif engine == "crawl4ai":
        if isinstance(native.get("error"), str):
            errors.append(native["error"])
        extracted = native.get("extracted_content")
        if isinstance(extracted, list):
            errors.extend(row.get("content") for row in extracted
                          if isinstance(row, dict) and row.get("tags") == ["error"])
    if not errors and status is None:
        return None
    return {"source": "native-extractor-error", "engine": engine,
            "http_status": status, "error": " ".join(str(v) for v in errors if v is not None)}


def prepare_environment():
    """Compile once at startup and share only immutable file-hash hints, not SDK state."""
    roots = sorted({sysconfig.get_paths()[key] for key in ("purelib", "platlib")})
    compiled = run_worker({}, command=[sys.executable, "-m", "compileall", "-q", "-f",
                                      *roots, __file__])
    if compiled.returncode:
        raise RuntimeError("Installed extraction bytecode preparation failed: "
                           + compiled.stdout + compiled.stderr)
    fingerprint = installed_environment_sha256()
    store = tempfile.TemporaryDirectory(prefix="extract-attestation-")
    target = Path(store.name) / "files.json"
    raw = json.dumps([(str(path), signature, checksum)
                      for path, (signature, checksum) in _ENVIRONMENT_FILE_HASHES.items()]).encode()
    target.write_bytes(raw)
    return store, {"path": str(target), "sha256": hashlib.sha256(raw).hexdigest(),
                   "environment_sha256": fingerprint}


def load_environment_cache(receipt):
    global _ENVIRONMENT_FILE_HASHES
    if receipt is None:
        return
    raw = Path(receipt["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
        raise ValueError("Installed extraction file-hash cache changed")
    _ENVIRONMENT_FILE_HASHES = {
        Path(path): (tuple(signature), checksum) for path, signature, checksum in json.loads(raw)
    }


def worker(value):
    load_environment_cache(value.pop("_environment_cache", None))
    validate_input(value)
    started = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        if value["engine"] == "firecrawl":
            data, native = firecrawl_extract(value)
        elif value["engine"] == "scrapegraph":
            data, native = graph(value)
        else:
            data, native = asyncio.run(
                crawl(value) if value["engine"] == "crawl4ai" else stagehand_extract(value)
            )
    # This process serves one extraction. Recheck files, packages and settings
    # rather than returning the identity cached during input validation.
    local_runtime_identity.cache_clear()
    observed_runtime = runtime_identity(value["engine"])
    runtime_matches = observed_runtime == value["expected_runtime"]
    return {
        "success": isinstance(data, dict) and runtime_matches,
        "error": "Runtime changed during extraction"
        if not runtime_matches
        else (None if isinstance(data, dict) else "Native extraction did not return one record"),
        "data": data,
        "native": native,
        "native_failure": native_failure(value["engine"], native) if data is None else None,
        "runtime": observed_runtime,
        "usage": {
            "wall_seconds": time.monotonic() - started,
            "process_cpu_seconds": time.process_time(),
            "peak_rss_native_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "rss_unit": "bytes" if sys.platform == "darwin" else "KiB",
        },
    }


def terminate_children(parent):
    """Stop a request's tree before killing it, including adopted browser processes."""
    import psutil

    descendants = {}
    while True:
        found = parent.children(recursive=True)
        previous = set(descendants)
        for child in found:
            descendants[child.pid] = child
            try:
                child.suspend()
            except psutil.NoSuchProcess:
                pass
        if set(descendants) == previous:
            break
    for child in reversed(list(descendants.values())):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(list(descendants.values()), timeout=1)


def supervise(command):
    """Keep Linux orphans under one request, rather than the shared HTTP service."""
    import ctypes

    import psutil

    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot enable request child-process reaping")

    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, interrupted)
    try:
        child = subprocess.Popen(command)
        return child.wait()
    finally:
        for name in (signal.SIGTERM, signal.SIGINT):
            signal.signal(name, signal.SIG_IGN)
        terminate_children(psutil.Process())
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break


def run_worker(value, *, timeout=110, command=None):
    """Contain native browser descendants, including SDK-created sessions."""
    import psutil

    # Fail before spawning when the host cannot enumerate this process's children.
    psutil.Process().children(recursive=True)
    if command is None:
        value = {key: item for key, item in value.items() if key != "_environment_cache"}
        if _ENVIRONMENT_CACHE_RECEIPT is not None:
            value["_environment_cache"] = _ENVIRONMENT_CACHE_RECEIPT
    command = command or [sys.executable, "-m", "harness.web_extract.self_hosted"]
    if sys.platform == "linux":
        command = [sys.executable, "-m", "harness.web_extract.self_hosted", "--supervise", *command]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**{k: v for k, v in os.environ.items() if k != "PYTHONPYCACHEPREFIX"},
             "PYTHONDONTWRITEBYTECODE": "1"},
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(json.dumps(value), timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if sys.platform == "linux":
                process.terminate()
                process.communicate(timeout=3)
            else:
                terminate_children(psutil.Process(process.pid))
                process.kill()
                process.communicate(timeout=1)
        except (ProcessLookupError, psutil.NoSuchProcess, subprocess.TimeoutExpired):
            with contextlib.suppress(psutil.NoSuchProcess):
                terminate_children(psutil.Process(process.pid))
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                for stream in (process.stdout, process.stderr):
                    stream.close()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


async def read_request_body(request, *, limit=100_000):
    """Bound streamed and fixed-length requests before JSON decoding."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if size < 0:
            raise ValueError("Invalid Content-Length")
        if size > limit:
            raise OverflowError("Request too large")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise OverflowError("Request too large")
        body.extend(chunk)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Request must contain valid JSON") from exc


def create_app():
    from fastapi import FastAPI, Header, HTTPException, Request

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        global _ENVIRONMENT_CACHE_RECEIPT
        store, receipt = await asyncio.to_thread(prepare_environment)
        _ENVIRONMENT_CACHE_RECEIPT = receipt
        try:
            yield
        finally:
            _ENVIRONMENT_CACHE_RECEIPT = None
            store.cleanup()

    capacity = worker_capacity()
    app = FastAPI(title="Open-source Web Extract", docs_url=None, redoc_url=None, lifespan=lifespan)

    def authenticated(authorization):
        token = os.environ.get("EXTRACTION_API_KEY", "")
        return bool(token) and hmac.compare_digest(
            authorization.encode("utf-8"), ("Bearer " + token).encode("utf-8")
        )

    @app.get("/health")
    def health(authorization: str = Header(default="")):
        if not authenticated(authorization):
            return {"status": "ok"}
        return {
            "status": "ok",
            "engines": {
                e: runtime_identity(e)
                for e in ENGINES
                if e != "firecrawl" or os.environ.get("FIRECRAWL_BACKEND_URL")
            },
        }

    @app.post("/extract/{engine}")
    async def extract(
        engine: str,
        request: Request,
        authorization: str = Header(default=""),
        keepalive: bool = False,
    ):
        if not authenticated(authorization):
            raise HTTPException(401, "Invalid API key")
        try:
            value = await read_request_body(request)
            if not isinstance(value, dict):
                raise ValueError("Request must be a JSON object")
            value["engine"] = engine
        except OverflowError as exc:
            raise HTTPException(413, str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

        lease = acquire_worker_slot(capacity)
        if lease is None:
            raise HTTPException(
                429, "Extraction worker capacity reached", headers={"Retry-After": "1"}
            )
        validation = asyncio.create_task(asyncio.to_thread(validate_input, value))
        try:
            await asyncio.shield(validation)
        except BaseException as exc:
            def release_validation(done):
                lease.close()
                if not done.cancelled():
                    done.exception()
            if validation.done():
                release_validation(validation)
            else:
                validation.add_done_callback(release_validation)
            if isinstance(exc, (KeyError, ValueError)):
                raise HTTPException(400, str(exc)) from exc
            raise

        def owned_worker():
            try:
                return run_worker(value)
            finally:
                lease.close()

        async def failed(error, status=None):
            payload = {"success": False, "data": None, "error": error}
            if status is not None:
                payload["http_status"] = status
            try:
                payload["runtime"] = await asyncio.to_thread(runtime_identity, engine)
            except Exception as attestation_error:
                payload.update(
                    runtime=None, runtime_attestation_error=type(attestation_error).__name__
                )
            return payload

        async def result():
            try:
                completed = await asyncio.to_thread(owned_worker)
                if completed.returncode:
                    error = completed.stderr
                    for key in ("EXTRACTION_MODEL_API_KEY", "EXTRACTION_API_KEY"):
                        if os.environ.get(key):
                            error = error.replace(os.environ[key], "[REDACTED]")
                    return await failed(error[-6000:])
                payload = json.loads(completed.stdout)
                json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                return payload
            except subprocess.TimeoutExpired as exc:
                raise HTTPException(504, "Native extraction exceeded 110 seconds") from exc
            except Exception as exc:
                # Keep process/decoder diagnostics private. Cancellation remains
                # outside Exception and retains the existing cleanup path.
                raise HTTPException(500, "Native extraction worker failed") from exc

        task = asyncio.create_task(result())
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        if not keepalive:
            return await asyncio.shield(task)

        from fastapi.responses import StreamingResponse

        async def stream():
            try:
                # JSON permits leading whitespace. The complete response remains
                # one JSON document, while intermediate proxies keep receiving bytes.
                yield b"\n"
                while not task.done():
                    done, _ = await asyncio.wait({task}, timeout=KEEPALIVE_SECONDS)
                    if not done:
                        yield b"\n"
                try:
                    payload = task.result()
                except HTTPException as exc:
                    # Headers have already been sent; retain the native failure
                    # status inside the opt-in streaming result.
                    payload = await failed(exc.detail, exc.status_code)
                yield json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            finally:
                # A disconnected caller does not remove the worker's 110-second
                # deadline or process cleanup. Consume any eventual exception.
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())

        return StreamingResponse(
            stream(),
            media_type="application/json",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )

    return app


if __name__ == "__main__":
    if sys.argv[1:2] == ["--supervise"]:
        sys.exit(supervise(sys.argv[2:]))
    else:
        print(json.dumps(worker(json.load(sys.stdin)), default=str))
