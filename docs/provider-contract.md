# Web Fetch provider contract

A Web Fetch provider takes a task and timeout and returns a normalized result:

```python
fetch(case: EvalCase, timeout: float) -> ProviderResult
```

`ProviderResult.content` is saved even when it contains an API error. Return these fields when available:

- request success and HTTP status;
- fetched content;
- final URL and title;
- provider-specific request ID, engine, server elapsed time and billing data;
- a stable error type and actionable error message.

Credentials are expanded from environment placeholders in the provider config.
They must never be added to `metadata`, content, manifest or logs.

## Add an adapter

1. Implement `Provider` and register its type in `harness/web_fetch/providers/base.py`.
2. Add an example configuration and tests using local fixtures.
3. Assign its output contract in `methods.py`.

Use the existing scorer and report renderer.

### Hosted Markdown fetch services

Five methods share `providers/hosted_fetch.py` and the existing HTTP client,
runner and scorer. Their credentials and fixed options are declared in
`configs/providers.example.json`; credentials come from the repository `.env`.

| Method | Credentials | Native route and example options |
| --- | --- | --- |
| Bright Data | `BRIGHTDATA_API_TOKEN`, `BRIGHTDATA_UNLOCKER_ZONE` | `/request`, raw Markdown, rendering and native debug enabled |
| Browser Use Fetch | `BROWSER_USE_API_KEY` | `/fetch`, Markdown, fresh session, US proxy; no Agent |
| Browserbase Fetch | `BROWSERBASE_API_KEY` | `/v1/fetch`, Markdown, redirects and native proxy enabled; no Project ID |
| DeepSeek Fetch API | `DEEPSEEK_API_KEY` | `/v1/tools/fetch`, URL-only request; retain content and title |
| Crawl4AI Cloud | `CRAWL4AI_CLOUD_API_KEY` | `/scrape`, `format=md`, `engine=browser`, `proxy=none` |

These adapters implement webpage body/structure tasks. Unsupported document,
media and file tasks fail before transport, without helper downloads or a
substitute browser/Agent. Rendering and internal retries remain service-owned;
offline adapter checks do not establish JavaScript capability or task pass rates.
Alternative structured formats require a separately declared output contract;
never derive schemas or parameters from expected answers.

Retain complete native responses separately from score-bearing answer fields.
An API HTTP 200 does not prove target retrieval succeeded: explicit service or
origin errors, empty content and malformed responses remain failures. API status
is distinct from target-site status. `log_level: debug` saves redacted caller
requests, response headers/body and errors; it cannot expose undisclosed service
internals. Only Bright Data receives the native debug option.

`dns_mode: system` uses the local resolver for API hosts; target-site DNS belongs
to the service. No automatic DNS fallback is added. Browser Use receives the call
budget through `timeout_ms`; the other services retain their native defaults
within the outer deadline. Historical runs keep their recorded configuration.

For diagnostics without scoring, use `run --collect-only --runs 1
--max-failure-retries 0`. It preserves responses and logs, but cannot produce a
scored report without explicit rescoring and does not certify a cohort.

API references: [Bright Data](https://docs.brightdata.com/api-reference/rest-api/unlocker/unlock-website),
[fetch-use](https://browser-use.com/posts/fetch-use),
[Browserbase Fetch](https://docs.browserbase.com/platform/fetch/overview),
[Crawl4AI Cloud](https://api.crawl4ai.com/docs/).

| Output contract | Comparison scope |
| --- | --- |
| Cleaned content: `main_text`, Markdown or text | Shared readable-content leaderboard |
| Rendered HTML | Separate diagnostics |
| Search results, model-bound URL context or structured AI extraction | Separate datasets |

## Native browsers and HTML conversion

Native browser configurations use `headless_markdown` or `headless_html`, with
an explicit `engine`, `binary` and `expected_version`. Method names are labels;
they do not select executable versions. The worker ships in the Python package,
so these adapters also run from an installed wheel outside the checkout.
Native Markdown routes do not require Docker. Resource measurement requires the
Docker extra and a declared image containing the same worker bytes as the host.

HTML conversion requires Node.js and `node-html-markdown` 2.0.0. A checkout uses
the package installed by `npm ci`. Installed-wheel users must install that npm
package separately and set `node_html_markdown_path` to its absolute package
directory in the provider configuration. The adapter rejects a missing or
different version before calls; the converter itself ships in the wheel.

## Third-party tools and library workers

Repository-native third-party workers ship under
`harness/web_fetch/workers/` in both source and wheel distributions. Commands
and `required_paths` use `{harness_root}/harness/web_fetch/workers/<worker>`;
the same configuration resolves package assets outside a checkout. Python
library workers require the `oss-eval` extra and an explicit interpreter from
that environment. Upstream checkouts, Node.js, package managers and browser
binaries remain caller-provided; they are not bundled with the workers.

Native methods require the upstream checkout and runtime versions declared in
the selected configuration. Copy the example configuration and update its pins
to match the intended experiment; preflight rejects mismatched installations.
After changing the DSH worker,
rerun `scripts/bootstrap_oss_baselines.sh` to refresh its deployed copy. Preflight
rejects a copy whose bytes differ from the maintained worker before any calls.
DSH request status comes from the tool's declared output or presentation metadata;
text embedded in the returned page cannot supply a missing HTTP status.

## Test the installed package

`make package-check` executes the installed TypeScript bridges with synthetic
upstream tool responses. Its `oss-eval` installation also retrieves a local HTML
page through LangChain and a local attachment through Crawl4AI, checking success
and HTTP failure. These fixtures do not contact public websites or use API keys.

The general CLI builds a result report from the supplied dataset and records.
It does not require project-specific articles, account registries, pricing or
adoption snapshots. Formal repository builds select those materials explicitly
after validating their bound profile and evidence.

## Task delivery and DOM completion

Moli selects one output route from the task's delivery contract before applying
page-family extraction rules. File tasks return bytes; resource tasks return
resource URLs even on document or challenge pages. The command, decoder and
recorded route describe the same selection. Acceptance answers and task labels
do not become execution arguments.

Dynamic DOM completion applies to text and resource discovery independently of
output format, including selector/count, frame, delay and timeout constraints.
The version-2 extraction scope describes text extraction and requires the
dynamic text route. Combining it with resource discovery is rejected before a
call. Direct file delivery also rejects DOM completion: it cannot claim a
rendered-page condition was satisfied while fetching only response bytes.

## Crawl4AI Cloud and the local library

`crawl4ai-cloud` and local `crawl4ai` are separate methods: old local results
cannot be relabeled as Cloud results. The local worker uses headless Chrome,
cache bypass, `domcontentloaded`, overlay removal, iframe processing and
`raw_markdown`. Cloud returns service-cleaned Markdown with no exact counterpart
for every local option. Browser versions, extraction and site access may differ.
Retain Cloud engine/cache/usage fields in the raw response. A Cloud 403 can mean
target access denial, not only invalid credentials; native browser traces are
not promised.

### Selected fixed-native comparison policy

The example configuration selects fixed native parameters for `moli-webfetch`,
`firecrawl`, `cloudflare-markdown`, `tavily`, `exa`, `brightdata`,
`browser-use-fetch`, `browserbase`, `deepseek-fetch-api`, and `crawl4ai-cloud`.
Requests do not consult scoring requirements. Per-case retrieval hints are
rejected before transport; dynamic cases must declare the native retrieval mode.
Exa uniformly requests up to 100 native rich links. Cloudflare receives the URL
directly, including document URLs; the harness does not download a file for it.
Unsupported native routes remain unsupported rather than receiving caller help.
The full 30-second deadline covers the provider response and required decoding;
scorer input preparation, scoring and evidence persistence follow afterward.
A failure retry receives another 30 seconds. The final call supplies the result
and primary latency, while all physical answers and cumulative retry cost remain
recorded. A still-running timed-out call is quarantined instead of overlapped.

### Reusable selected cohort

`configs/providers.example.json` declares the ten retained methods in
`default_providers`. Use `--providers configured --provider-config
configs/providers.example.json` with the existing `run`, `validate`,
`run_benchmark.py` or `run_certified_benchmark.py` entry point. Selection is
resolved before execution and frozen as explicit method names in run evidence.
The shared defaults are 30 seconds per physical call and one failure retry;
explicit CLI overrides remain available and are recorded. The no-argument
example still uses the offline fixture. Other adapters remain available by name.

The selected HTTP adapters retain a 20,000,000-byte response ceiling, and Moli
retains its output ceiling. This is a caller evidence limit, not a native page
or subresource download setting. Moli receives no website-specific private
network exception or caller-specified download size cap. Every selected method
enables caller debug logs. Credentials remain in the repository `.env`; Moli's
local path must resolve to the version and hash pinned in the configuration.

For a diagnostic collection, supply a frozen dataset and a new external output:

```sh
uv run --frozen python scripts/run_benchmark.py \
  --dataset /path/to/frozen-dataset.json \
  --provider-config configs/providers.example.json --providers configured \
  --timeout 30 --max-failure-retries 1 --workers 1 --measure-only \
  --output /path/to/new-run
```

Publishable cohorts use `scripts/run_certified_benchmark.py` with the same
provider selection and timing arguments, plus its required case scope, work
root and output. Report-instance profiles and measured data remain outside Git.
