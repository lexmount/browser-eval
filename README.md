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

Run the small, credentialed real smoke suite (three calls total: CowAgent,
Lexmount Global and Firecrawl against `example.com`) explicitly:

```bash
COWAGENT_REPO=/path/to/CowAgent ./scripts/run_real_smoke.sh
```

Normal unit tests skip these network calls. The smoke runner loads credentials
from `~/.env` by default; override with `WEBFETCH_SMOKE_ENV_FILE=/path/to/.env`.

## Run real Web Fetch methods

```bash
./scripts/run_webfetch_eval.sh \
  --env-file ~/.env \
  --providers cowagent-built-in,lexmount-global,firecrawl \
  --runs 3 \
  --timeout 45 \
  --delay 1 \
  --output runs/core-$(date -u +%Y%m%dT%H%M%SZ)
```

The command performs the requests, persists every response or exception,
calculates metrics and creates the report. Rebuild a report without making
network calls:

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
    ├── providers/                 # one detail page per method
    ├── metrics/index.html         # formal metric contract
    ├── market/index.html          # direct vs adjacent supplier capability boundary
    ├── adoption/index.html        # full high-star/recent GitHub candidate table
    ├── integration/index.html     # measured CowAgent and cross-agent integration path
    ├── accounts/index.html        # registration and credential checklist
    ├── attempts/                  # content, failures and root-cause evidence
    └── artifacts/                 # complete fetched content, available offline
```

An HTTP 200 with missing expected content is shown as a **content failure**, not
as a successful fetch. Network exceptions and API errors retain their response
or traceback. This makes the report usable both for comparison and initial
incident diagnosis.

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
