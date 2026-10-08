# Browser task-native Web Fetch comparison

The separate frozen-HTML public-data comparison is documented in
[WEBMAINBENCH.md](WEBMAINBENCH.md); its fidelity scores are not mixed with
the live-URL task acceptance or resource profile below.

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
| Moli 1.1.1 | local CLI | ordinary pages: `fetch --dump markdown`; direct documents/media: bare `fetch URL`, then timed text extraction only for document-body tasks |
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

CPU, resident memory and network are a required, separate performance profile,
not optional decorations on one release. The resource profile uses a
provider-blind 20-page ordinary-web cohort, one provider call at a time, and a
fresh disposable Linux container with a dedicated bridge network for every task.
The host controller and container startup/teardown are excluded. The container
is limited to two logical CPUs, 2 GiB memory and 512 PIDs. CPU and network totals
come from cumulative cgroup/container counters; memory and rate percentiles are
sampled every 50 ms. The published local-engine comparison uses only the
intersection of tasks passed by all four local configurations. A missing CPU,
memory, network or Docker/cgroup-v2 environment field blocks packaging and publication.

This scope is client-side. For four local configurations it includes the browser,
worker and conversion processes in the container. Kitesurf runs remotely, so its
client-only measurements cannot enter the local-engine table; Cloudflare-side
CPU, memory and upstream traffic are unavailable and must never be inferred as zero. Host-process
sampling and whole-machine interface deltas are diagnostic-only and cannot enter
the published resource comparison. Shorter-than-sample memory and sampled-rate
peaks remain a disclosed limit; cumulative CPU and network totals do not depend
on catching a sampling instant.

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

Local binary paths use `MOLI_BINARY`, `LIGHTPANDA_BINARY`,
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

The historical `evaluation/dataset.json` input is resolved through
`evaluation/source-inputs.json`; its full text and captured sources are outside
Git. Import the approved frozen copy before using the historical commands below,
as described in [reference inputs](../../docs/reference-inputs.md).
`evaluation/dataset-catalog.json` is a public task directory, not an execution
dataset. An explicitly supplied custom dataset remains a normal JSON file.

`harness/web_fetch/headless_runner.py` owns cohort selection, execution and
implementation checks. The command in `scripts/` delegates to it.
`configs/providers.headless.json` declares the method registry and the `main`
and `resources` cohorts. A new browser release changes this configuration,
without adding a runner or a browser-name branch. Historical evidence keeps
its original configuration hash.

Each cohort declares an ordered `providers` list and may supply
`provider_overrides`. The resource cohort declares its container image and
container binary paths; its Moli 1.1.1 pin is independent of the main cohort's
Moli 1.1.5 pin. `expected_version` matches the first numeric version in the
observed version output. A missing or different pinned version fails before
provider calls. Both cohorts retain executable hashes and reject changes
during execution.

Use `--cohort NAME` to select a declared cohort and `--providers NAME1,NAME2`
to select registered methods explicitly. A configuration without cohorts uses
all its registered methods. Custom configurations supply their own resource
image and binary paths; the runner does not infer them from method names.
Unknown or duplicate methods, task IDs and task families are rejected before
calls. The execution receipt records the selected cohort and actual input
filenames rather than claiming the repository's default inputs.

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

Use `--provider-config /absolute/path/providers.json` to select one provider
configuration for the entire requested scope. The certified wrapper forwards
it to the shared runner while retaining all reference checks. Without this
option, the repository's existing task-group configurations apply. Diagnostic
`scripts/run_benchmark.py` runs support the same option and remain uncertified.

After selecting and freezing the resource cohort, run it separately and keep the
same postcheck and independent all-attempt review gates:

```sh
make resource-image
uv run --frozen python scripts/run_headless_benchmark.py --dataset RESOURCE/dataset.json --output RESOURCE_RUN --workers 1 --resource-sample-interval-ms 50
uv run --frozen python scripts/postcheck_headless.py --run-dir RESOURCE_RUN --captures RESOURCE_POST --browser-observations RESOURCE_POST.json
uv run --frozen python scripts/certify_headless_cases.py --run-dir RESOURCE_RUN --observations RESOURCE_POST/observations.json --output RESOURCE_CERTIFICATES
uv run --frozen python scripts/review_headless_results.py --evidence RESOURCE_RUN --work RESOURCE_REVIEW --batch-cases 1 --workers 4 --model EXACT_AVAILABLE_MODEL --scope all --install
uv run --frozen python scripts/package_headless_resources.py --run RESOURCE_RUN
```

Build the resource image once per declared image definition or browser-version
change. Benchmark runs reuse `lex-web-fetch-resource:local` by image ID and do
not rebuild it; repeated `make resource-image` calls reuse Docker layers. A
publication run records the image ID and executable hashes, so an unavailable
or changed image fails closed instead of triggering an implicit download.
Validation also compares the packaged worker hash with the worker inside the
declared image before calls. Rebuild the image after worker changes; an older
worker cannot be reported as the selected host runtime.

The resource cohort dataset and package bind its size, providers, concurrency,
sample interval, cgroup-v2 backend, CPU/memory limits, network isolation,
required metrics, image ID and executable hashes. Do not substitute the
10-method `evaluation/current-report.json` profile for this separate cohort.
Headless resource packaging fails if CPU, memory or network evidence is missing;
the report release check requires grouped winners whenever the cohort is present.

The resource-mode default is the four local methods: Lightpanda, Obscura, Moli
1.1.1, and Chromium. Kitesurf remains in the correctness and latency report but
is excluded from the resource table because its remote browser CPU, memory, and
upstream network traffic are not observable from the client.

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
evidence bundle fixes the dataset ID, task count, provider set, timeout and
route description; `scripts/check_headless_evidence.py` rejects a different
cohort. The repository's current publication profile now points at the
10-method Web Fetch report and must not be inferred from this headless cohort.
Review requirements are bound to the declared evidence scope: unresolved
judgments block publication, and a failure-only review discloses both reviewed
attempts and accepted unreviewed machine passes. Assembly retains inherited
review history only when its scope and judgments match the final results. These checks are
covered by the repository test suite; a new session must repair the evidence or
implementation instead of bypassing them.

The preserved public destination for this comparison is the separate app-domain
Sites project `UUAhhQMnrGwm`. The current 10-method report is the separate
project declared by `evaluation/current-report.json`; never use a headless
receipt or evidence bundle to update it.
