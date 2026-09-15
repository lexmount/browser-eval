# WebMainBench-545 preselected-200 five-browser comparison

The current browser-native Web Fetch report evaluates Kitesurf, Lightpanda,
Obscura, Moli 1.1.5 and Chromium on a 200-page sample selected and frozen from
the public WebMainBench-545 release before provider results existed. It is a
frozen-HTML-to-Markdown fidelity benchmark,
not a live-URL access benchmark and not the paper's separate 7,809-page
ROUGE-5 leaderboard.

## Frozen input and reference

The source JSONL must have SHA-256
`0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e`
and exactly 545 unique `track_id` values. Every record's `html` and
`groundtruth_content` fields must be non-empty.

Each method receives the exact UTF-8 bytes of the official `html` field.
Lightpanda, Obscura, Moli and Chromium read those bytes from a loopback HTTPS
server under a macOS sandbox that permits only loopback networking. Each
batch uses a dedicated ephemeral CA; the HTTPS scheme prevents page code from
redirecting merely because the fixture protocol differs from the source URL. The response
CSP permits inline scripts from the frozen bytes but blocks external resources.
Kitesurf receives the same bytes through Cloudflare Browser Run's documented
inline `html` input with external subresource types rejected.
The original URL is retained only as provenance metadata.

WebMainBench HTML contains public `cc-select` annotation attributes. None of
the five generic converters is customized to read those attributes, but the
cue is inherent in the released dataset and must be disclosed.

## Measured routes

| Method | Deployment | Route |
|---|---|---|
| Kitesurf | Cloudflare Browser Run | `POST /browser-rendering/markdown?browser=kitesurf` with inline HTML |
| Lightpanda | local CLI | `fetch --dump markdown` |
| Obscura | local CLI | `fetch --dump markdown --allow-private-network` for the loopback fixture |
| Moli 1.1.5 | local CLI | `fetch --dump markdown --wait-until done` |
| Chromium 152 | local browser | complete DOM HTML converted by `node-html-markdown` 2.0.0 |

The comparison aligns input bytes, output contract, case set and the 45-second
physical-call deadline. It does not claim identical implementations or
deployment paths. Remote Kitesurf latency includes the service and network;
local latency includes process/browser startup and conversion. Therefore the
latency columns are observed client wall time, not an engine-only CPU result.

## Official scoring

Scoring is pinned to WebMainBench commit
`9d991bdc00c57b57521499494d96be85c31317ba` and uses the official deterministic
`use_llm=false` configuration. The report publishes `text_edit`, `code_edit`,
`formula_edit`, `table_edit`, `table_TEDS` and `overall`, the arithmetic mean
of the five independently aggregated component metrics.

The official implementation aggregates only successful calculations for each
component. Thus component denominators differ and are part of the result.
Extraction failures do not produce component metrics in the official evaluator;
request success and failure classes are reported separately.

The scorer's documented labels are not always a complete behavioral
description. In this pinned revision, `text_edit` compares the complete
Markdown strings. Table labels in the released truth may wrap long prose in an
HTML `<table>`; a converter that emits that prose without table syntax can score
zero on the table metrics. These are properties of the official data and code,
not locally corrected rules.

## Retry and evidence rules

The run predeclares at most one retry after a failed physical call. Every
physical response is retained. The final physical call is authoritative even
when an earlier one was better, and `total_elapsed_ms` includes both calls and
any provider-required retry wait. A correction for a wrong binary, command,
input, scorer or timing implementation is not a retry: preserve the old evidence
and supersede the complete affected scope.

Provider outputs live outside Git under `~/.browser-eval/`. Every result binds
the input, reference and output bytes by SHA-256. Runtime manifests bind local
binaries and the runner; Moli must report 1.1.5 and Obscura 0.2.2. A moving
Lightpanda nightly is identified by its retained binary hash when no immutable
version string is available.

## Credentials

Only Kitesurf requires account credentials in the ignored repository-root
`.env`:

```dotenv
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
```

The token needs Browser Rendering / Browser Run edit access. Values,
authorization headers, cookies and user paths must never enter Git or the
public report. Artifact Hub publishing uses its separate host-scoped token
lookup and does not read `.env`.

## Reproduce

Prepare the locked environment and run one immutable shard per method:

```sh
make setup
uv run --frozen python scripts/run_webmainbench_benchmark.py \
  --provider moli \
  --case-ids-file evaluation/public-webmainbench-200.json \
  --output ~/.browser-eval/evidence/webmainbench-545-five-browser-YYYYMMDD/moli
```

Repeat with `kitesurf`, `lightpanda`, `obscura` and `chromium`. Existing output
directories are rejected rather than overwritten. Score each completed shard:

```sh
uv run --frozen --extra webmainbench-eval python scripts/score_webmainbench_results.py \
  --evidence ~/.browser-eval/evidence/webmainbench-545-five-browser-YYYYMMDD/moli \
  --dataset ~/.browser-eval/public-benchmarks/webmainbench-545/WebMainBench_545.jsonl \
  --scorer-repo ~/.browser-eval/public-benchmarks/webmainbench-545/webmainbench-code
```

Run the independent audit across all five shards, then assemble only a passed
matrix:

```sh
uv run --frozen --extra webmainbench-eval python scripts/review_webmainbench_results.py \
  --source-root ~/.browser-eval/evidence/webmainbench-545-five-browser-YYYYMMDD \
  --dataset ~/.browser-eval/public-benchmarks/webmainbench-545/WebMainBench_545.jsonl \
  --sample-manifest evaluation/public-webmainbench-200.json \
  --output ~/.browser-eval/evidence/webmainbench-545-five-browser-YYYYMMDD/result-review.json

uv run --frozen python scripts/assemble_webmainbench_evidence.py \
  --source-root ~/.browser-eval/evidence/webmainbench-545-five-browser-YYYYMMDD \
  --dataset ~/.browser-eval/public-benchmarks/webmainbench-545/WebMainBench_545.jsonl \
  --sample-manifest evaluation/public-webmainbench-200.json \
  --review ~/.browser-eval/evidence/webmainbench-545-five-browser-YYYYMMDD/result-review.json \
  --output ~/.browser-eval/evidence/current

make report EVIDENCE=~/.browser-eval/evidence/current
make report-check
```

The final audit independently checks the complete 1,000-result matrix, every
artifact/input hash, physical retry ordering and elapsed-time sum, component
denominators and provider aggregates. It also recomputes text, code and formula
edit scores without importing the official scorer. Any disagreement blocks the
build and publication.

`evaluation/current-report.json` is the only current matrix and publication
authority. The destination site is `UUAhhQMnrGwm` on
`https://artifact-hub.app.lexmount.com`; older report sites must not be updated.
