# Browser task-native Web Fetch comparison

The cohort reuses all 150 registered Web Fetch task IDs: 100 web cases and 10
each for document, image, video, challenge and dynamic delivery. Every method
receives the same URL, task rule and end-to-end deadline and is judged by the
shared `score_attempt` path. Implementations are deliberately not forced through
one extractor: the benchmark asks each method to use its fastest documented or
built-in route to the task's final deliverable. Web and document-body tasks end
in readable text; direct-file tasks end in verified original bytes.

## Runtime and route

| Method | Deployment | Measured route |
|---|---|---|
| Kitesurf | Cloudflare account Browser Run | `POST /browser-rendering/markdown?browser=kitesurf` |
| Lightpanda | local CLI | `fetch --dump markdown` |
| Obscura | local CLI, standard mode | `fetch --dump markdown` |
| Moli 1.0.6 | local CLI | ordinary pages: `fetch --dump markdown`; direct documents/media: bare `fetch URL`, then timed text extraction only for document-body tasks |
| Moli 1.1.1 | local CLI | ordinary pages: `fetch --dump markdown`; direct documents/media: bare `fetch URL`, then timed text extraction only for document-body tasks |
| Moli 1.1.3 | local CLI | ordinary pages: `fetch --dump markdown`; direct documents/media: bare `fetch URL`, then timed text extraction only for document-body tasks |
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
No method receives task answers, task-specific selectors, clicks, scrolling,
personal sessions, stealth fallback or model post-processing. The original
published matrix used one provider call per task and did not retry; later
documented corrections may supersede affected logical results while preserving
the earlier physical evidence.
Future runs may predeclare `--max-failure-retries 1`: only a failed task may
receive one additional physical call, both artifacts are retained, total
logical latency includes the retry path, and the final physical call is always
authoritative. Choosing the better response after seeing outcomes is forbidden.

## What is and is not fair

The comparison aligns inputs, outputs, task set, acceptance rules and the
client-observed end-to-end deadline. It does not claim identical internals.
Forcing native Markdown methods through HTML conversion would replace their
product advantage with benchmark code and answer a less useful question.

Kitesurf is remote while the other six methods are local (covering four local
implementation families). Its latency includes network
round trips, Cloudflare queueing and remote execution; local measurements
include local process startup and machine contention. This is a cold-call
client latency comparison, not an engine-only CPU/RSS benchmark. The original
matrix's single observation per method and URL does not establish variance,
sustained throughput or SLA. Documented correction calls remain visible and do
not turn that matrix into a repeated-measures performance study.
Pairwise browser tables use only URLs passed by both methods and keep each
pair's denominator visible. A wider heterogeneous-method same-window cohort
must not be reselected after observing results merely to manufacture a common
success intersection. It retains every predeclared call and reports the
same-denominator all-call latency beside success counts and separately labeled
per-method successful-call latency.

Method-specific readiness behavior can change delivered content. Equal option
names would still not prove equal implementation, so the report records the
actual condition instead of calling it normalized. Documents and media also
have different delivery contracts. A document-body case measures URL to readable
text: a provider's direct text is accepted, while a provider that returns raw
PDF/DOCX bytes must parse them inside the same end-to-end deadline. A direct-file
case stops at verified bytes and never performs an irrelevant Markdown conversion.
The run does not add OCR, media understanding or a generic downloader that bypasses
the selected method.

## Credentials and binaries

Evaluation credentials load only from this repository's ignored `.env`.
Kitesurf needs a Cloudflare account ID and API token with Browser Rendering /
Browser Run access (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`). The report
lists official registration and token pages and variable names only. It never
copies a real key into HTML, source control or examples.

Local binary paths use `MOLI_1_0_6_BINARY`, `MOLI_BINARY`, `MOLI_1_1_3_BINARY`, `LIGHTPANDA_BINARY`,
`OBSCURA_BINARY` and `CHROMIUM_BINARY`. Set `CHROMIUM_BINARY` explicitly: an
empty value is rejected so a later partial rerun cannot silently switch from
the report's browser to Playwright's bundled Chromium. Binaries are not committed; measured binary hashes and
versions are retained in call evidence. Install JavaScript dependencies with
`npm install` and the Python benchmark dependencies in a repository-local
environment.

Every local call is bound to the batch executable SHA-256. The runner captures
that identity before and after the timed calls, writes it into the execution
manifest and rejects any binary or reported-version change. Identity probing
is outside the latency boundary. A mutable release label or
configured path is never treated as an identity. If a CLI exposes no version
or immutable commit (for example a moving nightly asset), the SHA-256 remains
the exact executable identity and the missing source revision stays explicit.
A version recovered later by matching a retained executable to an official
archive is labelled post-hoc; it is not rewritten as run-time provenance.

## Run and preserve evidence

The reference lifecycle in [README.md](README.md) is mandatory. Independently
capture ordinary pages, separately observe browser-dependent complete scopes,
freeze the references, execute, then postcheck:

```sh
uv run --frozen python scripts/freeze_headless_references.py --captures CAPTURES --output FROZEN --browser-observations PRE.json
uv run --frozen python scripts/run_headless_benchmark.py --dataset FROZEN/dataset.json --output RUN --workers 5
uv run --frozen python scripts/postcheck_headless.py --run-dir RUN --captures POST --browser-observations POST.json
uv run --frozen python scripts/certify_headless_cases.py --run-dir RUN --observations POST/observations.json --output CERTIFICATES
uv run --frozen python scripts/review_headless_results.py --evidence EVIDENCE --work REVIEW --batch-cases 5 --workers 4 --model EXACT_AVAILABLE_MODEL --install
```

For new `run_benchmark.py` cohorts, use the fail-closed wrapper so an aggregate
run cannot be mistaken for publishable evidence before its worker manifests
and top-level execution file carry verified postchecks:

```sh
uv run --frozen python scripts/run_certified_benchmark.py --dataset evaluation/dataset.json --output RUN --work-root WORK --providers METHOD --case-ids ID1,ID2 --browser-binary /absolute/path/to/chromium
```

Failure retry is disabled by default. Add `--max-failure-retries 1` only when
the protocol calls for one retry before measurement begins. This is distinct
from correcting a proven wrong command, implementation version, scorer or
reference: a correction explicitly supersedes the affected logical results,
reruns the complete declared comparison scope and records its score impact.

A failed or incomplete evidence directory is immutable. A reference failure
suspends that case's new body measurement; it is not a provider failure. A
replacement cohort must be independently prefrozen and must cover every method
in the affected comparison unless a method-specific implementation change is
the declared replacement reason. Historical calls are never retroactively
certified. Equivalent independent captures may be assembled only when reference
bytes, normalized hash, URL, task contract, reference scope and rendered scope
are identical; each physical batch retains its exact capture selector and
postcheck.

Do not rerun the whole matrix merely because code changed. Use the smallest
scope that preserves a valid comparison: presentation-only changes rebuild the
report; scoring-only changes rescore immutable outputs; one changed reference
reruns that case for every method; one implementation change reruns all cases
for that method; a timing-boundary change reruns the all-method speed cohort.
A full matrix rerun is required only when a global task input, call boundary or
shared implementation dependency changes. The assembly manifest must retain
which physical calls were replaced and why.

Result verification is an opt-in stage after assembly: do not start it until the
user explicitly requests review. The default scope is machine failures; use
`--scope all --confirm-full-review` only after an explicit full-review request.
A strong agent compares every selected output with the task, frozen standard
answer or stable contract without trusting the machine verdict. Disagreements require
an explicit adjudication; unresolved rows block report generation. The review
file is cryptographically bound to the exact dataset and results files, so a
new session cannot silently reuse an audit after outputs or rules change.

Measured outputs live outside Git under `~/.browser-eval/evidence/`. Git retains
the canonical dataset, source annotations, adapters and report implementation.
Build from an explicit immutable evidence directory:

```sh
make setup
uv run --frozen python scripts/check_headless_evidence.py EVIDENCE --rescore
make report EVIDENCE=/absolute/path/to/evidence
make report-check
```

`.python-version`、`uv.lock`、Makefile 与 CI 共同固定 Python 3.11 和依赖版本。
正式命令不得改用系统 `python` / `python3`；环境不满足时应立即失败，不能降级执行。

## Cross-session release gates

The headless report remains a separately published reference cohort. Its
evidence bundle fixes the dataset ID, task count, seven providers, timeout and
route description; `scripts/check_headless_evidence.py` rejects a different
cohort. The repository's current publication profile may point at another
report and must not be inferred from this historical reference document.
When the current publication profile requires review, the report requires an
exact binding for the user-requested scope, zero unresolved judgments, valid
reference/scoring receipts and a nonempty top-level review-round log. A
failure-only review must disclose its reviewed count and accepted unreviewed
machine passes. Assembly rebinds inherited review history only when its declared
scope and judgments still match the final dataset/results bytes. These checks are
covered by the repository test suite; a new session must repair the evidence or
implementation instead of bypassing them.

The preserved public destination for this comparison is the separate app-domain
Sites project `UUAhhQMnrGwm`. The current 12-method report is the dev-domain
project declared by `evaluation/current-report.json`; never use a headless
receipt or evidence bundle to update it.
