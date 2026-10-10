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
