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

`configs/providers.example.json` includes `brightdata`, `browser-use-fetch`,
`browserbase`, `deepseek-fetch-api` and `crawl4ai-cloud`. These adapters use `providers/hosted_fetch.py`, the shared HTTP client,
and the existing runner/scorer. They support webpage body/structure tasks only;
file and resource tasks fail explicitly without making a provider call.

| Method | Credentials in repository `.env` | Selected route |
| --- | --- | --- |
| Bright Data | `BRIGHTDATA_API_TOKEN`, `BRIGHTDATA_UNLOCKER_ZONE` | Web Unlocker `/request`, `format=raw`, `data_format=markdown`, native `render="true"` in the example configuration |
| Browser Use | `BROWSER_USE_API_KEY` | fetch-use `/fetch`, `output_format=markdown`, new session, US proxy, service retry defaults preserved; no Agent |
| Browserbase | `BROWSERBASE_API_KEY` | `/v1/fetch`, `format=markdown`, redirects enabled, native proxies enabled by default; no Project ID required |
| DeepSeek Fetch API | `DEEPSEEK_API_KEY` | `/v1/tools/fetch`, request body contains only `url`; title and Markdown content are preserved separately |

DeepSeek Fetch exposes no documented render, wait, retry or debug request option.
Its example configuration enables the caller's observed HTTP log, not invented
service internals. Actual exceptions include their runtime traceback; deadline
events are recorded when the caller enforces its answer cutoff. The API HTTP
status is not the target website's status. The complete response envelope is
retained even for failed API requests.

For connectivity/capability checks while the scorer is being revised, use the
existing `run` command with `--collect-only --runs 1 --max-failure-retries 0`.
This mode retains the same calls, deadline, raw artifacts and logs, but never
invokes scoring or creates a scored report. Its summary has no task pass rate;
references are copied for later use without claiming current qualification.
Collection-only evidence must be explicitly rescored before a scored report can
be built. This diagnostic mode does not certify a benchmark cohort.

Browserbase Markdown uses Fetch Extract billing and does not execute JavaScript.
Bright Data supports a native `render` flag on the same Web Unlocker endpoint.
The adapter accepts an optional Boolean `render` configuration and sends the
documented string `"true"`/`"false"`; omission preserves provider selection.
The example enables it uniformly before calls, without caller-supplied selectors,
extra waiting or browser code. Rendering may increase latency and does not
guarantee that Markdown excludes unrelated page sections. Browser Use's fetch-use
SDK exposes no render/wait selector option; its guide directs JavaScript-dependent
tasks to the separate browser product. Do not silently replace either Fetch
method with a browser session or Agent and keep the same method identity.
Native output alternatives can be declared separately: `output_format=structured`
for fetch-use, or `output_format=json` with a JSON Schema `schema` for Browserbase.
The latter uses the service's own extraction; the adapter only serializes the
returned object, never fills missing fields. Freeze schemas before calls, without
reference values, answer counts or source scripts. These are separate output
contracts from the default Markdown comparison; do not silently substitute a
later alternative for an earlier failed response. Record the selected format in
each result. A missing JavaScript renderer alone does not prove a task impossible:
embedded HTML data may be extracted through another documented native route.
Bright Data manages unlocking/retries internally; its raw response does not
expose the target HTTP status, so that field stays unavailable. The API HTTP
status is recorded separately for these hosted methods. Complete response envelopes
are retained as private hash-bound evidence, separate from score-bearing text.
Empty, malformed, binary and failed target responses cannot become successful
page retrievals merely because the service returned HTTP 200.

The example configurations enable `log_level: debug` for these hosted methods.
This activates the shared caller log: timestamped request parameters (with API
credentials redacted), actual HTTP response headers/body, and completion/errors.
The runner retains these private logs separately from scored answers. This is
observed client traffic, not a trace of undisclosed service internals. Bright Data
also receives its documented native `debug: true` option; any returned
`x-brd-debug` and error headers are retained verbatim in the HTTP log. Browser Use
Fetch and Browserbase Fetch have no documented equivalent in the inspected Fetch
contracts; no invented debug flag or browser-session fallback is sent. Logs can
identify exposed request IDs, API failures and returned content, but cannot prove
an internal rendering or retry cause that the service does not expose. Previously
collected runs without this setting have no retroactive verbose log.

These adapters use the full caller timeout (30 seconds in the CLI by default),
without reserving 10 percent. The outer answer deadline includes API connection,
transfer and answer decoding; scoring and report generation run afterward.
Browser Use receives that same timeout through its native `timeout_ms` parameter;
the other APIs keep their service-side defaults and are bounded by the caller.
The shared runner allows one declared failure retry by default. Each physical
call gets its own 30-second deadline; the final response is authoritative and all
physical calls and cumulative execution cost are retained. The adapters add no
hidden caller retries. Service-internal retries are not overridden; their actual count
is unknown unless exposed by the service. A successful retry inside the service
still has to return within the same outer deadline.

No adapter applies task-specific retrieval hints or silently switches to a
browser or Agent. No caller code fetches the target URL, alternate URLs or data
endpoints to complete an answer. Product-native redirect handling, Markdown
conversion, unlocking and configured proxies are allowed. Freeze one configuration
for the whole declared scope before calls; do not tune individual tasks from
reference answers or observed failures. A diagnostic run can therefore expose
dynamic-page limitations.
Select a predeclared task scope with the existing CLI, for example:

```bash
uv run --frozen browser-harness-eval run \
  --dataset /absolute/path/to/dataset.json \
  --provider-config configs/providers.example.json \
  --providers brightdata,browser-use-fetch,browserbase \
  --case-ids case-a,case-b --provider-concurrency 1 --max-failure-retries 0
```

`dns_mode` defaults to `system`; the example configurations use the local system
resolver without extra public-DNS queries or automatic DNS fallback. Only API
hostnames are resolved by the caller; target-site DNS stays with each service.
An explicitly declared `public_doh` setting
uses the existing thread-scoped DNS helper, retaining HTTPS hostname/certificate
verification and DNS observations. It does not change system settings or add a
retrieval fallback. Apply the same DNS policy to compared methods and include
its overhead in observed latency. Historical runs preserve their original DNS,
timeout and proxy configuration; changing defaults does not update their scores
or make their results measurements of the new configuration.

API contracts: [Bright Data request and render parameter](https://docs.brightdata.com/api-reference/rest-api/unlocker/unlock-website),
[fetch-use](https://browser-use.com/posts/fetch-use),
[Browserbase Fetch](https://docs.browserbase.com/platform/fetch/overview).

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

`crawl4ai-cloud` uses the shared hosted adapter with `CRAWL4AI_CLOUD_API_KEY`
from the repository `.env`. It is a separate method identity from `crawl4ai`;
local measurements must not be relabeled as cloud measurements.
The example configuration selects `POST /scrape` with `format=md`,
`engine=browser`, and `proxy=none`. Browser mode requests a fresh browser fetch
instead of the cloud's automatic cache/HTTP/browser routing. No country is forced.
Caller DNS is `system`; this does not configure the service's target-site DNS.
The call budget is a run parameter, defaulting to 30 seconds per physical call.
The shared runner permits one failure retry by default, with a fresh call budget.
Internal service retries remain service-owned. Historical 45-second calls remain unchanged.

The local worker uses headless Chrome, cache bypass, `domcontentloaded`,
overlay removal, iframe processing, and `raw_markdown`. Cloud exposes no exact
counterparts for those wait/extraction settings and returns service-cleaned
`markdown`; do not claim identical browser versions, output, or site access.
Only webpage body/structure tasks are implemented by this adapter; PDF/media/file
support needs its own declared and tested contract before use.

Retain the complete native JSON, including its engine/cache/usage fields, alongside
unchanged Markdown and caller debug logs. No undocumented native debug flag is
sent. An API HTTP 200 alone is insufficient: an explicit error/false success,
invalid Markdown field, empty content, or explicit origin failure is a failure.
Cloud 403 is access denial, not necessarily invalid credentials: documented
causes also include target-site blocks. Native browser traces are not promised.

Official contract: https://api.crawl4ai.com/docs/ (checked 2026-10-23).

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
