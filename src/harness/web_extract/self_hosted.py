"""Native open-source extraction workers and a small authenticated HTTP boundary."""

import asyncio
import contextlib
import hashlib
import hmac
import importlib.metadata
import json
import os
import resource
import signal
import subprocess
import sys
import sysconfig
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

ENGINES = {
    "crawl4ai": "crawl4ai",
    "scrapegraph": "scrapegraphai",
    "stagehand": "stagehand",
    "firecrawl": None,
}


def validate_input(value):
    """Restrict collection to the declared immutable snapshot origins."""
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
    if value.get("engine") not in ENGINES:
        raise ValueError("Unknown extraction engine")
    if value["engine"] == "firecrawl" and not os.environ.get("FIRECRAWL_BACKEND_URL"):
        raise ValueError("Firecrawl backend is not configured")
    if value.get("expected_runtime") != runtime_identity(value["engine"]):
        raise ValueError("Runtime differs from the frozen extraction configuration")
    return value


def installed_environment_sha256():
    """Ignore the caller's working directory when fingerprinting installed packages."""
    paths = sorted({sysconfig.get_paths()[key] for key in ("purelib", "platlib")})
    packages = sorted(
        (str(d.metadata.get("Name", "unknown")), str(d.version))
        for d in importlib.metadata.distributions(path=paths)
    )
    return hashlib.sha256(json.dumps(packages).encode()).hexdigest()


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
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment_sha256": installed_environment_sha256(),
    }
    if engine in ("scrapegraph", "stagehand"):
        identity["browser_version"] = subprocess.check_output(
            [os.environ["CHROME_PATH"], "--version"],
            text=True,
            timeout=5,
        ).strip()
    return identity


def runtime_identity(engine):
    identity = dict(local_runtime_identity(engine))
    if engine == "firecrawl":
        import requests

        response = requests.get(
            os.environ["FIRECRAWL_IDENTITY_URL"],
            timeout=5,
            allow_redirects=False,
        )
        response.raise_for_status()
        backend = response.json()
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
        force_json_response=True,
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
    data = {k: v for k, v in item.items() if k in value["schema"]["properties"]}
    return data, {"extracted_content": native, "usage": vars(strategy.total_usage)}


def graph(value):
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
        timeout=150,
    )
    extractor = SmartScraperGraph(
        prompt=value.get("instruction", "Extract the requested fields of the main record."),
        source=value["url"],
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
    return result, {"answer": result, "execution": extractor.get_execution_info()}


async def stagehand_extract(value):
    from json_schema_to_pydantic import create_model
    from openai import AsyncOpenAI
    from stagehand import (
        LLMStructuredGenerateParams,
        LLMStructuredGenerateResult,
        Stagehand,
        local_browser,
    )

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

        browser = await local_browser.launch(
            headless=True,
            chromium_sandbox=False,
            **(
                {"executable_path": os.environ["CHROME_PATH"]}
                if os.environ.get("CHROME_PATH")
                else {}
            ),
        )
        try:
            extractor = await Stagehand.create(browser=browser, model=generate, cache=False)
            try:
                page = (await browser.context.pages())[0]
                await page.goto(value["url"])
                result = await extractor.extract(
                    value.get("instruction", "Extract the fields of the main record."),
                    create_model(value["schema"]),
                )
                return result.data.model_dump(mode="json"), {
                    "extract": result.model_dump(mode="json"),
                    "model_calls": calls,
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


def worker(value):
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
    observed_runtime = runtime_identity(value["engine"])
    runtime_matches = observed_runtime == value["expected_runtime"]
    return {
        "success": isinstance(data, dict) and runtime_matches,
        "error": "Runtime changed during extraction"
        if not runtime_matches
        else (None if isinstance(data, dict) else "Native extraction did not return one record"),
        "data": data,
        "native": native,
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
    command = command or [sys.executable, "-m", "harness.web_extract.self_hosted"]
    if sys.platform == "linux":
        command = [sys.executable, "-m", "harness.web_extract.self_hosted", "--supervise", *command]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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

    app = FastAPI(title="Open-source Web Extract", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "engines": {
                e: runtime_identity(e)
                for e in ENGINES
                if e != "firecrawl" or os.environ.get("FIRECRAWL_BACKEND_URL")
            },
        }

    @app.post("/extract/{engine}")
    async def extract(engine: str, request: Request, authorization: str = Header(default="")):
        token = os.environ.get("EXTRACTION_API_KEY", "")
        if not token or not hmac.compare_digest(
            authorization.encode("utf-8"), ("Bearer " + token).encode("utf-8")
        ):
            raise HTTPException(401, "Invalid API key")
        try:
            value = await read_request_body(request)
            if not isinstance(value, dict):
                raise ValueError("Request must be a JSON object")
            value["engine"] = engine
            await asyncio.to_thread(validate_input, value)
        except OverflowError as exc:
            raise HTTPException(413, str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            completed = await asyncio.to_thread(
                run_worker,
                value,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "Native extraction exceeded 110 seconds") from exc
        if completed.returncode:
            error = completed.stderr
            for key in ("EXTRACTION_MODEL_API_KEY", "EXTRACTION_API_KEY"):
                if os.environ.get(key):
                    error = error.replace(os.environ[key], "[REDACTED]")
            error = error[-6000:]
            return {
                "success": False,
                "error": error,
                "runtime": await asyncio.to_thread(runtime_identity, engine),
            }
        return json.loads(completed.stdout)

    return app


if __name__ == "__main__":
    if sys.argv[1:2] == ["--supervise"]:
        sys.exit(supervise(sys.argv[2:]))
    else:
        print(json.dumps(worker(json.load(sys.stdin)), default=str))
