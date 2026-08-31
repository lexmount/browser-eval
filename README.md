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

## Run real Web Fetch methods

```bash
./scripts/run_webfetch_eval.sh \
  --env-file ~/.env \
  --providers http-local,lexmount-global,firecrawl,jina-reader \
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
  --providers lexmount-global,firecrawl,jina-reader
```

## Credentials

The example provider registry reads these optional environment variables:

| Provider | Required variables | Notes |
|---|---|---|
| Lexmount global | `LEXMOUNT_API_KEY`, `LEXMOUNT_PROJECT_ID` | Must belong to the `.com` region/project |
| Lexmount China | `LEXMOUNT_CN_API_KEY`, `LEXMOUNT_CN_PROJECT_ID` | Kept separate to prevent silent cross-region retries |
| Firecrawl | `FIRECRAWL_API_KEY` | Limited anonymous calls may work; production evaluation should use an account |
| Jina Reader | `JINA_API_KEY` | Optional for low-rate access |
| HTTP local | None | Standard-library static HTTP/HTML baseline |
| Fixture | None | Deterministic offline installation check |

Dotenv files are parsed as data and are never executed. Credentials are used in
request headers only and are not written to manifests, JSONL, artifacts or HTML.

Print the complete registration checklist, official key locations, current
credential readiness and a copyable `.env` template:

```bash
python3 -m browser_harness_eval accounts --env-file ~/.env
```

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

## Scope

The core dataset contains public, no-login sites and deliberately avoids
targeting anti-bot systems. Only evaluate authenticated or protected targets
when authorized, and record that policy in a separate dataset.
