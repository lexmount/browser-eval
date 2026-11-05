"""Native open-source extraction workers and a small authenticated HTTP boundary."""

import asyncio
import contextlib
import hashlib
import hmac
import importlib.metadata
import json
import os
import resource
import subprocess
import sys
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
        or not schema.get("properties")
    ):
        raise ValueError("An object JSON Schema with properties is required")
    if value.get("engine") not in ENGINES:
        raise ValueError("Unknown extraction engine")
    if value["engine"] == "firecrawl" and not os.environ.get("FIRECRAWL_BACKEND_URL"):
        raise ValueError("Firecrawl backend is not configured")
    if value.get("expected_runtime") != runtime_identity(value["engine"]):
        raise ValueError("Runtime differs from the frozen extraction configuration")
    return value


@lru_cache(maxsize=len(ENGINES))
def runtime_identity(engine):
    return {
        "engine": engine,
        "version": importlib.metadata.version(ENGINES[engine])
        if ENGINES[engine]
        else os.environ["FIRECRAWL_IMAGE"],
        "model": os.environ["EXTRACTION_MODEL"],
        "model_base": os.environ["EXTRACTION_MODEL_BASE"],
        "model_context_tokens": int(os.environ.get("EXTRACTION_MODEL_CONTEXT_TOKENS", "128000")),
        "implementation": "native-open-source/1",
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment_sha256": hashlib.sha256(
            json.dumps(
                sorted(
                    (str(d.metadata.get("Name", "unknown")), str(d.version))
                    for d in importlib.metadata.distributions()
                )
            ).encode()
        ).hexdigest(),
    }


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
        raise RuntimeError(result.error_message or "Crawl4AI fetch failed")
    native = json.loads(result.extracted_content)
    # The task asks for one record. Never choose the best of multiple candidates.
    item = native[0] if isinstance(native, list) and len(native) == 1 else native
    if not isinstance(item, dict) or item.get("error"):
        raise RuntimeError("Crawl4AI did not return exactly one record: " + json.dumps(native))
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
    response.raise_for_status()
    native = response.json()
    if native.get("success") is not True:
        raise RuntimeError("Firecrawl extraction failed: " + json.dumps(native))
    return native["data"]["json"], native


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
    return {
        "success": True,
        "data": data,
        "native": native,
        "runtime": runtime_identity(value["engine"]),
        "usage": {
            "wall_seconds": time.monotonic() - started,
            "process_cpu_seconds": time.process_time(),
            "peak_rss_native_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "rss_unit": "bytes" if sys.platform == "darwin" else "KiB",
        },
    }


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
        if not token or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Invalid API key")
        if int(request.headers.get("content-length", "0")) > 100_000:
            raise HTTPException(413, "Request too large")
        value = await request.json()
        value["engine"] = engine
        try:
            validate_input(value)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "harness.web_extract.self_hosted"],
                input=json.dumps(value),
                text=True,
                capture_output=True,
                timeout=115,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "Native extraction exceeded 115 seconds") from exc
        if completed.returncode:
            error = completed.stderr[-6000:]
            for key in ("EXTRACTION_MODEL_API_KEY", "EXTRACTION_API_KEY"):
                if os.environ.get(key):
                    error = error.replace(os.environ[key], "[REDACTED]")
            return {"success": False, "error": error, "runtime": runtime_identity(engine)}
        return json.loads(completed.stdout)

    return app


if __name__ == "__main__":
    print(json.dumps(worker(json.load(sys.stdin)), default=str))
