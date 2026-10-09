import asyncio
import io
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REQUEST = {"url": "https://example.invalid/", "timeout_seconds": 45,
           "uniform_native_parameters": True, "track": "web", "task_kind": "body",
           "dns_mode": "system", "retrieval_plan": None}


def test_langchain_native_requests_have_no_timeout_override():
    seen = []
    class Loader:
        def __init__(self, **kwargs): seen.append(kwargs)
        def load(self): return [SimpleNamespace(page_content="all native text", metadata={})]
    with patch.dict(sys.modules, {"langchain_community.document_loaders": SimpleNamespace(WebBaseLoader=Loader)}), \
         patch("importlib.metadata.version", return_value="fixture"), \
         patch("sys.stdin", io.StringIO(json.dumps(REQUEST) + "\n")), \
         patch("sys.stdout", new_callable=io.StringIO) as output:
        runpy.run_path(str(ROOT / "harness/web_fetch/workers/langchain_webbase_worker.py"))
    assert seen[0]["requests_kwargs"] == {}
    assert len(seen) == 1
    assert json.loads(output.getvalue())["content"] == "all native text"


def test_crawl4ai_retains_native_capabilities_and_page_timeout_default():
    seen = []
    def run_config(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(page_timeout=60000, **kwargs)
    class Crawler:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def arun(self, **kwargs):
            assert kwargs["config"].page_timeout == 60000
            return SimpleNamespace(success=True, markdown=SimpleNamespace(raw_markdown="all native text"),
                downloaded_files=[], status_code=200, url=REQUEST["url"], redirected_url=None, redirected_status_code=None,
                error_message=None, media={}, links={}, model_dump=lambda **kw: {"native": "complete"})
    fake = {"crawl4ai": SimpleNamespace(AsyncWebCrawler=Crawler, BrowserConfig=lambda **kw: kw,
                CacheMode=SimpleNamespace(BYPASS="bypass"), CrawlerRunConfig=run_config),
            "crawl4ai.async_configs": SimpleNamespace(HTTPCrawlerConfig=object),
            "crawl4ai.async_crawler_strategy": SimpleNamespace(AsyncHTTPCrawlerStrategy=object),
            "crawl4ai.processors.pdf": SimpleNamespace(PDFContentScrapingStrategy=object, PDFCrawlerStrategy=object)}
    with patch.dict(sys.modules, fake), patch("importlib.metadata.version", return_value="fixture"), \
         patch("sys.stdin", io.StringIO(json.dumps(REQUEST) + "\n")), \
         patch("sys.stdout", new_callable=io.StringIO) as output:
        worker = runpy.run_path(str(ROOT / "harness/web_fetch/workers/crawl4ai_worker.py"))
        asyncio.run(worker["main"]())
    assert len(seen) == 1
    assert "page_timeout" not in seen[0]
    assert seen[0]["process_iframes"] is True
    assert seen[0]["remove_overlay_elements"] is True
    assert not {"js_code", "wait_for"} & seen[0].keys()
    result = json.loads(output.getvalue())
    assert result["ok"], result
    assert result["content"] == "all native text"


def test_typescript_workers_keep_native_timers(tmp_path):
    import os
    import shutil
    import subprocess
    import pytest
    node = os.environ.get('OPENCLAW_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node runtime unavailable')
    major = int(subprocess.check_output([node, '--version'], text=True).strip().lstrip('v').split('.')[0])
    if major < 24:
        pytest.skip('Node 24 type stripping required')
    stub = tmp_path / 'native-fixture.mjs'
    stub.write_text('''
export const SystemPrompt = {}, ToolRuntime = {}, WebRuntime = {}, TimeoutPolicy = {};
export const ToolWeb = {name: 'tool-web'};
export const ToolCallId = x => x;
const seen = {};
export const WebFetchHttp = {
  name: 'web-fetch-http', LOCAL_FETCH_PROVIDER_ID: 'native',
  Config: options => {
    if ('timeoutMs' in options) throw Error('caller overrode native timeout');
    return {timeoutMs: 30000, ...options};
  }
};
export class Context {
  fiber = {dispose: async () => {}};
  async plugin(module, options) {
    if (module.name) seen[module.name] = options;
  }
  tools = {execute: async () => {
    if (seen['web-fetch-http'].timeoutMs !== 30000) throw Error('wrong native default');
    if ('fetchTimeoutMs' in seen['tool-web']) throw Error('tool timeout override');
    return {isError: false, value: {statusCode: 200, url: 'https://example.invalid/',
      body: {kind: 'text', content: 'native answer'}}, content: [], seen};
  }};
}
export function createWebFetchTool(options) {
  if ('timeoutSeconds' in options.config.tools.web.fetch) throw Error('internal timeout override');
  return {execute: async (id, args) => ({details: {text: 'native answer', status: 200}, options, args})};
}
''')
    source = (ROOT / 'harness/web_fetch/workers/openclaw_webfetch_worker.mts').read_text()
    fixture_root = tmp_path / 'openclaw' / 'src/agents/tools'
    fixture_root.mkdir(parents=True)
    (fixture_root / 'web-fetch.ts').write_text(f'export {{ createWebFetchTool }} from {json.dumps(stub.as_uri())};')
    open_worker = tmp_path / 'openclaw.mts'
    open_worker.write_text(source)
    env = dict(os.environ, OPENCLAW_REPO=str(tmp_path / 'openclaw'), BROWSER_EVAL_HARNESS_ROOT=str(ROOT))
    request = json.dumps(REQUEST) + '\n'
    result = subprocess.run([node, str(open_worker)], input=request, text=True, capture_output=True, env=env, timeout=5, check=True)
    answer = json.loads(result.stdout)
    assert answer['ok'], answer
    assert answer['raw_tool_response']['options']['config']['tools']['web']['fetch']['cacheTtlMinutes'] == 0
    source = (ROOT / 'harness/web_fetch/workers/dsh_webfetch_worker.mts').read_text()
    source = '\n'.join(line for line in source.splitlines() if '@deepseek-ai/' not in line or not line.startswith('import '))
    source = f'import {{Context, ToolCallId, SystemPrompt, ToolRuntime, WebRuntime, WebFetchHttp, ToolWeb, TimeoutPolicy}} from {json.dumps(stub.as_uri())};\n' + source
    deep_worker = tmp_path / 'deepseek.mts'
    deep_worker.write_text(source)
    result = subprocess.run([node, str(deep_worker)], input=request, text=True, capture_output=True, env=env, timeout=5, check=True)
    answer = json.loads(result.stdout)
    assert 'raw_tool_response' in answer, answer
    assert answer['raw_tool_response']['seen']['web-fetch-http']['timeoutMs'] == 30000
