# Five-browser fastest-Markdown comparison

The cohort reuses all 150 registered Web Fetch task IDs: 100 web cases and 10
each for document, image, video, challenge and dynamic delivery. Every method
receives the same URL, task rule and end-to-end deadline and is judged by the
shared `score_attempt` path. Implementations are deliberately not forced through
one extractor: the benchmark asks each method to deliver Markdown through its
fastest documented or built-in route.

## Runtime and route

| Method | Deployment | Measured Markdown route |
|---|---|---|
| Kitesurf | Cloudflare account Browser Run | `POST /browser-rendering/markdown?browser=kitesurf` |
| Lightpanda | local CLI | `fetch --dump markdown` |
| Obscura | local CLI, standard mode | `fetch --dump markdown` |
| Moli | local CLI | `fetch --dump markdown` |
| Chromium | local Playwright/CDP | rendered DOM HTML → `node-html-markdown` 2.0.0 |

Chromium and CDP do not expose a native Markdown result. Chromium Reader Mode
produces simplified HTML and does not cover this benchmark's full output
contract. The selected fallback therefore captures the complete rendered DOM
and converts it with the maintained MIT-licensed `node-html-markdown` package.
The converter version, file hash, Node version, source HTML hash and conversion
time are retained per call. Conversion is inside the 45-second deadline.

Kitesurf is not treated as a local open-source engine. As tested, it is a
Cloudflare-hosted preview selected by `browser=kitesurf`; Cloudflare has stated
an intention to open source it, which is not the same as released source code.
The public report must describe the currently available account API, not a
hypothetical local deployment.

The routes have different completion controls because those controls belong to
the tested interfaces: Kitesurf uses `networkidle2`, Chromium uses Playwright
`domcontentloaded`, and each CLI uses its own documented completion behavior.
No method receives task answers, task-specific selectors, retries chosen from a
failed score, clicks, scrolling, personal sessions, stealth fallback or model
post-processing.

## What is and is not fair

The comparison aligns inputs, outputs, task set, acceptance rules and the
client-observed end-to-end deadline. It does not claim identical internals.
Forcing native Markdown methods through HTML conversion would replace their
product advantage with benchmark code and answer a less useful question.

Kitesurf is remote while four methods are local. Its latency includes network
round trips, Cloudflare queueing and remote execution; local measurements
include local process startup and machine contention. This is a cold-call
client latency comparison, not an engine-only CPU/RSS benchmark. One invocation
per method and URL does not establish variance, sustained throughput or SLA.
Pairwise latency tables therefore use only URLs passed by both methods and keep
each pair's denominator visible.

Method-specific readiness behavior can change delivered content. Equal option
names would still not prove equal implementation, so the report records the
actual condition instead of calling it normalized. Documents and media also
have different delivery contracts; this run does not silently add a PDF parser,
OCR, media understanding or generic downloader to any method.

## Credentials and binaries

Evaluation credentials load only from this repository's ignored `.env`.
Kitesurf needs a Cloudflare account ID and API token with Browser Rendering /
Browser Run access (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`). The report
lists official registration and token pages and variable names only. It never
copies a real key into HTML, source control or examples.

Local binary paths use `MOLI_BINARY`, `LIGHTPANDA_BINARY`, `OBSCURA_BINARY` and
`CHROMIUM_BINARY`. Binaries are not committed; measured binary hashes and
versions are retained in call evidence. Install JavaScript dependencies with
`npm install` and the Python benchmark dependencies in a repository-local
environment.

## Run and preserve evidence

The reference lifecycle in [README.md](README.md) is mandatory. Independently
capture ordinary pages, separately observe browser-dependent complete scopes,
freeze the references, execute, then postcheck:

```sh
python scripts/freeze_headless_references.py --captures CAPTURES --output FROZEN --browser-observations PRE.json
python scripts/run_headless_benchmark.py --dataset FROZEN/dataset.json --output RUN --workers 5
python scripts/postcheck_headless.py --run-dir RUN --captures POST --browser-observations POST.json
python scripts/certify_headless_cases.py --run-dir RUN --observations POST/observations.json --output CERTIFICATES
```

A failed or incomplete evidence directory is immutable. A reference failure
suspends that case's new body measurement; it is not a provider failure. A
replacement cohort must be independently prefrozen and must cover every method
in the affected comparison unless a method-specific implementation change is
the declared replacement reason. Historical calls are never retroactively
certified. Equivalent independent captures may be assembled only when reference
bytes, normalized hash, URL, task contract, reference scope and rendered scope
are identical; each physical batch retains its exact capture selector and
postcheck.

Measured outputs live outside Git under `~/.browser-eval/evidence/`. Git retains
the canonical dataset, source annotations, adapters and report implementation.
Build from an explicit immutable evidence directory:

```sh
python scripts/check_headless_evidence.py EVIDENCE --rescore
python scripts/build_report.py --evidence EVIDENCE --output reports/webfetch
python scripts/check_report.py reports/webfetch
python scripts/check_report_links.py reports/webfetch
python scripts/check_report_revision.py reports/webfetch
```

The public destination for this comparison is the separate site
`bWkRP157EtF6`. Do not update the older report site `UeZE1vfcEOrM`.
