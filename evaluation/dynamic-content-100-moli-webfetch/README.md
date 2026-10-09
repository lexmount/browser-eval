# Dynamic-content 100-case local comparison

The current local comparison uses these 100 frozen cases with ten methods:
Moli WebFetch 1.1.15, Firecrawl, Tavily, Exa, Cloudflare Markdown, CowAgent,
OpenClaw, DeepSeek Harness, LangChain WebBaseLoader and Crawl4AI. Each method
has one authoritative result per case: 1,000 results in total. The compact
`current-local-results.json` binds the exact saved result set and measured
scoring revision; it is not a provider execution manifest or publication target.

The caller deadline is 45 seconds, with no caller failure retry or per-case
retrieval hints. Product timers retain their native defaults. Firecrawl uses
its native `onlyMainContent: false` option uniformly across its 100 cases.
The shared scorer accepts equivalent Markdown list and table formats.

Measured responses, logs, execution manifests, corrections and generated HTML
stay outside Git under `BROWSER_EVAL_DATA_HOME` or `~/.browser-eval/`.
Only the current 1,000 logical results are selected. Superseded Moli releases,
old Firecrawl outputs and preliminary smoke runs remain private historical
evidence and are not part of this comparison.

To render the bound evidence without making provider calls, first use a checkout
of the scoring Git revision recorded in `current-local-results.json`. Run the
following command from that checkout; the current branch intentionally rejects
older scores without an explicit offline revision receipt:

```bash
uv run --frozen python scripts/build_report.py \
  --local-audit \
  --evidence "$WEB_FETCH_EVIDENCE_DIR" \
  --output /tmp/dynamic-100-local-report
```

Use the scoring revision bound in `current-local-results.json` when reproducing
historical scores; a newer scorer requires an explicit offline revision receipt.
The result set is a dated local diagnostic comparison, not a certified release:
sources were not recaptured for these calls, and the caller/evidence audit does
not constitute an independent blind review of all passing results.
