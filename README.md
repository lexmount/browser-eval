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

The fixture intentionally fails the JavaScript case, so the generated report
contains both successful content and a complete failure diagnostic page.

Run the small, credentialed real smoke suite (one `example.com` call per
configured real method, including both Cloudflare contracts) explicitly:

```bash
COWAGENT_REPO=/path/to/CowAgent ./scripts/run_real_smoke.sh
```

Normal unit tests skip these network calls. The smoke runner loads credentials
from `~/.env` by default; override with `WEBFETCH_SMOKE_ENV_FILE=/path/to/.env`.

## Run real Web Fetch methods

```bash
./scripts/run_webfetch_eval.sh \
  --env-file ~/.env \
  --providers cowagent-built-in,lexmount-global,firecrawl,tavily,exa,cloudflare-markdown,cloudflare-content \
  --runs 3 \
  --timeout 45 \
  --delay 1 \
  --output runs/core-$(date -u +%Y%m%dT%H%M%SZ)
```

The command performs the requests, persists every response or exception,
calculates metrics and creates the report. Cloudflare `/markdown` belongs to
the readable-content leaderboard; `/content` returns rendered HTML and is shown
as a separate diagnostic contract.

For a complete new-environment run, use the one-command reproducer. It validates
credentials, runs the frozen dataset, builds the multi-page report and checks
all local links:

```bash
COWAGENT_REPO=/path/to/CowAgent \
  WEBFETCH_ENV_FILE=~/.env \
  ./scripts/reproduce_report.sh runs/reproduced
```

Set `WEBFETCH_RUN_SMOKE=1` to run the real smoke suite first. The script
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
| Firecrawl | `FIRECRAWL_API_KEY` | Limited anonymous calls may work; production evaluation should use an account |
| Tavily Extract | `TAVILY_API_KEY` | Known-URL Markdown/plain-text extraction |
| Exa Contents | `EXA_API_KEY` | Known-URL contents; config forces live crawl with `max_age_hours: 0` |
| Cloudflare `/markdown` | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | Token needs Browser Rendering Edit permission |
| Cloudflare `/content` | same as `/markdown` | Rendered HTML diagnostic, not scored against cleaned Markdown size/noise |
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
    ├── analysis/index.html        # dynamic ranking and case-level root-cause entry points
    ├── execution/index.html       # exact reproduction and incident runbook
    ├── metrics/index.html         # formal metric contract
    ├── market/index.html          # direct vs adjacent supplier capability boundary
    ├── adoption/index.html        # full high-star/recent GitHub candidate table
    ├── integration/index.html     # measured CowAgent and cross-agent integration path
    ├── accounts/index.html        # registration and credential checklist
    ├── attempts/                  # content, failures and root-cause evidence
    └── artifacts/                 # complete fetched content, available offline
```

An HTTP 200 with missing facts is shown as a **content failure**, not as a
successful fetch. Strict content requires all frozen fact groups, minimum
length, and no error/challenge marker. Each attempt shows a short preview, a
collapsed full report copy, an exact raw artifact and its SHA-256.

The v2 ground truth was independently annotated from source pages before the
supplier run. See [review log](docs/ground-truth-review.md). A second human
review is still recommended before external marketing or procurement use.

## Extend the harness

To add a dataset, copy `datasets/public_webfetch.json` and define stable URLs,
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
