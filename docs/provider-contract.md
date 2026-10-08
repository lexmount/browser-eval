# Web Fetch provider contract

A provider implements:

```python
fetch(case: EvalCase, timeout: float) -> ProviderResult
```

`ProviderResult.content` is always persisted, including API error bodies.
Providers should return normalized fields where available:

- request success and HTTP status;
- fetched content;
- final URL and title;
- provider-specific request ID, engine, server elapsed time and billing data;
- a stable error type and actionable error message.

Credentials are expanded from environment placeholders in the provider config.
They must never be added to `metadata`, content, manifest or logs.

To add another supplier, implement `Provider`, register the type in
`harness/web_fetch/providers/base.py`, add an example configuration and write a fixture-backed
unit test. Metric and report code should not require changes.

Providers are also assigned an output contract in `methods.py`. Cleaned
readable content (`main_text`, Markdown or text) shares one leaderboard.
Rendered HTML is a separate diagnostic contract; search results, model-bound
URL context and structured AI extraction require separate datasets.

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

Repository-native third-party workers require the pinned upstream checkout and
runtime declared in the provider configuration. After changing the DSH worker,
rerun `scripts/bootstrap_oss_baselines.sh` to refresh its deployed copy. Preflight
rejects a copy whose bytes differ from the maintained worker before any calls.
DSH request status comes from the tool's declared output or presentation metadata;
text embedded in the returned page cannot supply a missing HTTP status.

The general CLI builds a result report from the supplied dataset and records.
It does not require project-specific articles, account registries, pricing or
adoption snapshots. Formal repository builds select those materials explicitly
after validating their bound profile and evidence.
