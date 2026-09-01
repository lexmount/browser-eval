# Browser Harness Eval

Reproducible evaluation for agent browser harness tools. `web_fetch` is the
first implemented tool; the artifact model is designed to support browser-use
and authenticated interaction datasets later.

The repository separates four concerns:

- `datasets/`: what is tested and the deterministic acceptance criteria;
- `browser_harness_eval/providers/`: how each supplier or local method is called;
- `browser_harness_eval/metrics.py`: how output is measured and aggregated;
- `browser_harness_eval/report.py`: how persisted artifacts become a formal,
  multi-page HTML report.
- `report-assets/`: project-bound visual summaries embedded in generated reports.

## Quick start: verify a new environment offline

No third-party Python package is required.

```bash
git clone git@github.com:lexmount/browser-harness-eval.git
cd browser-harness-eval

python3 -m unittest discover -s tests -v
./scripts/run_webfetch_eval.sh \
  --providers fixture \
  --runs 1 \
  --delay 0 \
  --output runs/offline-smoke

open runs/offline-smoke/report/index.html
```

The fixture intentionally fails configured cases, so the generated report
contains both successful content and a complete failure diagnostic page.

Run the small, credentialed real smoke suite (one `example.com` call per
configured real method) explicitly:

```bash
COWAGENT_REPO=/path/to/CowAgent ./scripts/run_real_smoke.sh
```

Normal unit tests skip these network calls. The smoke runner loads credentials
from `~/.env` by default; override with `WEBFETCH_SMOKE_ENV_FILE=/path/to/.env`.

## Run real Web Fetch methods

```bash
./scripts/run_webfetch_eval.sh \
  --env-file ~/.env \
  --providers cowagent-built-in,lexmount-global,lexmount-cn,firecrawl,tavily,exa,cloudflare-markdown \
  --runs 3 \
  --timeout 45 \
  --delay 1 \
  --output runs/core-$(date -u +%Y%m%dT%H%M%SZ)
```

The command performs the requests, persists every response or exception,
calculates metrics and creates the report. Only Cloudflare `/markdown` is tested:
it matches the readable-content contract. `/content` returns full rendered HTML
and is deliberately excluded from this Web Fetch comparison.

Cloudflare account plans are recorded as execution-environment evidence. A Free
account retest still hit a long-period quota after a 10.5-second completion
cooldown (`Retry-After: 65255`), so short delays could not fix it. After the
account was upgraded, an unthrottled 10-case smoke passed 10/10 and the complete
100-page run produced zero HTTP 429 responses. The example registry therefore no
longer applies a Cloudflare client cooldown; the checked-in report uses the paid
run and retains the Free incident as historical evidence.

Firecrawl Free documents 10 `/scrape` requests per minute. The example registry
therefore also applies a 6.1-second completion cooldown. Higher paid plans should
override these values to match their documented team-level limits; pacing is
part of the account environment, not a supplier content-quality score.

For a complete new-environment run, use the one-command reproducer. It validates
credentials, runs the frozen 100-page dataset and the separate file/media
dataset, builds the multi-page report and checks all local links:

```bash
COWAGENT_REPO=/path/to/CowAgent \
  WEBFETCH_ENV_FILE=~/.env \
  ./scripts/reproduce_report.sh runs/reproduced
```

The reproducer runs different providers in parallel (four by default) while
keeping cases sequential within each provider. This shortens the full run
without increasing a single account's QPS or sharing a provider instance across
threads. Set `WEBFETCH_PROVIDER_CONCURRENCY=1` for fully serial execution, or a
higher value when provider accounts and the runner have enough capacity.

By default this also installs and executes the real OpenClaw, DeepSeek Harness,
LangChain WebBaseLoader and Crawl4AI implementations. OpenClaw is run with Node
24.15.0 because the measured checkout rejects older Node 24 SQLite builds.
Set `WEBFETCH_INCLUDE_OSS=0` only when intentionally running the seven managed or
built-in methods. Override checkout locations with `OPENCLAW_REPO`,
`DEEPSEEK_HARNESS_REPO` and `CRAWL4AI_REPO`.

Set `WEBFETCH_RUN_SMOKE=1` to run the real smoke suite first. The script
sets `WEBFETCH_RUN_FILE_MEDIA=0` only when intentionally skipping the document
and resource leaderboard. The script
reproduces the process and report structure; live website/service responses and
latencies are time-dependent. Rebuild an already persisted run byte-for-byte
without making network calls:

```bash
python3 -m browser_harness_eval report runs/core-20260831T120000Z
```

Validate dataset and credentials without making network calls:

```bash
python3 -m browser_harness_eval validate \
  --env-file ~/.env \
  --providers cowagent-built-in,lexmount-global,firecrawl
```

## Credentials

The example provider registry reads these optional environment variables:

| Provider | Required variables | Notes |
|---|---|---|
| Lexmount Global | `LEXMOUNT_API_KEY`, `LEXMOUNT_PROJECT_ID` | This harness uses `api.lexmount.com` only |
| Lexmount CN | `LEXMOUNT_CN_API_KEY`, `LEXMOUNT_CN_PROJECT_ID` | Separate method using `api.lexmount.cn`; never falls back across regions |
| Firecrawl | `FIRECRAWL_API_KEY` | Limited anonymous calls may work; production evaluation should use an account |
| Tavily Extract | `TAVILY_API_KEY` | Known-URL Markdown/plain-text extraction |
| Exa Contents | `EXA_API_KEY` | Known-URL contents; config forces live crawl with `max_age_hours: 0` |
| Cloudflare `/markdown` | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | Token needs Browser Rendering Edit permission |
| HTTP local | None | Standard-library static HTTP/HTML baseline |
| Fixture | None | Deterministic offline installation check |
| CowAgent built-in | `COWAGENT_REPO` | Path to a CowAgent checkout; invokes its real `WebFetch` class |

Dotenv files are parsed as data and are never executed. Credentials are used in
request headers only and are not written to manifests, JSONL, artifacts or HTML.

Print the complete registration checklist, official key locations, current
credential readiness and a copyable `.env` template:

```bash
python3 -m browser_harness_eval accounts --env-file ~/.env
```

The same blank variable list is committed as `.env.example`; copy it rather
than writing credentials into the repository.

The same registry is rendered at `report/accounts/index.html` in every report.

## Report structure

The checked-in reference run is
[`reports/2026-09-01-webfetch-100-eleven-way/index.html`](reports/2026-09-01-webfetch-100-eleven-way/index.html).
It contains 100 frozen webpage cases × 11 methods = 1,100 attempts, raw artifacts, machine
verdicts, case-local HIL controls and derived-run provenance. A separate
document/resource section is embedded under `capabilities/` and includes its
own 88 persisted attempts. It distinguishes document text, verified resource
links, media discovery and original binary download; binary bytes decoded as
text are failures, not support.

Every run produces a portable directory:

```text
runs/<run-id>/
├── manifest.json
├── dataset.json
├── results.jsonl
├── summary.json
├── artifacts/<provider>/<case>/run-001.txt
└── report/
    ├── index.html                 # cross-provider summary
    ├── datasets/                  # dataset and case pages
    ├── environment/index.html     # runtime, parameters and limitations
    ├── methodology/index.html     # fairness, Cloudflare endpoint choice, judging protocol
    ├── providers/                 # one detail page per method
    ├── analysis/index.html        # ranking, filters and case-level HIL entry points
    ├── capabilities/index.html    # document text, document links, media resources and source-file tracks
    ├── execution/index.html       # exact reproduction and incident runbook
    ├── metrics/index.html         # formal metric contract
    ├── market/index.html          # participants, industry boundary, adoption evidence and OpenClaw/DSH baselines
    ├── integration/index.html     # measured CowAgent and cross-agent integration path
    ├── accounts/index.html        # registration and credential checklist
    ├── attempts/                  # task, answer key, output, machine verdict and HIL form
    └── artifacts/                 # complete fetched content, available offline
```

An HTTP 200 with missing facts is shown as a machine content failure, not as a
successful content fetch. The machine verdict requires all frozen fact groups,
minimum length, and no error/challenge marker. Main P50/P95 use only requests
that also pass this content gate. Each attempt—not a separate review queue—shows
the full task, reference answer, supplier output, deterministic verdict, HIL
form and SHA-256 so a reviewer can inspect possible false positives and false
negatives in context.

The default v5 suite contains exactly 100 webpage URLs: 9 protocol/capability
cases, 79 CC-BY-4.0 WCXB cases across seven page structures and distinct domains,
and 12 concrete render-quality seeds from `lightmount-dark`. Direct PDF, DOCX,
TXT, image and video inputs live in the separate capability suite. See
[review protocol](docs/ground-truth-review.md).
A second human review is required before external marketing or procurement use;
live-page drift is excluded rather than counted as a supplier failure.

## Extend the harness

To add a dataset, copy `datasets/public_webfetch_100.json` and define stable URLs,
capability categories, deterministic `expected_any` markers and policy scope.
Do not mix public extraction, login flows and anti-bot unlocking into one score.

To add a provider, implement the contract in
`browser_harness_eval/providers/`, register its type in `providers/base.py`, add
environment-based example configuration and write a fixture-backed test.

See:

- [Architecture](docs/architecture.md)
- [Provider contract](docs/provider-contract.md)
- [Metric definitions](docs/metrics.md)
- [Web Fetch market and Agent adoption](docs/webfetch-market-and-adoption.md)
- [Measured integration decisions](docs/integration.md)

## Scope

The core dataset contains public, no-login sites and deliberately avoids
targeting anti-bot systems. Only evaluate authenticated or protected targets
when authorized, and record that policy in a separate dataset.
