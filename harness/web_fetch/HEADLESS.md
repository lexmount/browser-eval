# Compare live-URL browser methods

For a first local run, use the [HTTP/Moli/Chromium example](../../examples/web_fetch/README.md). This guide covers the maintained live-URL benchmark and its resource measurements.

[Methods](#runtime-and-route) · [Comparison limits](#what-is-and-is-not-fair) · [Setup](#credentials-and-binaries) · [Run](#run-and-preserve-evidence) · [Release checks](#release-checks)

The separate frozen-HTML public-data comparison is documented in
[WEBMAINBENCH.md](WEBMAINBENCH.md); its fidelity scores are not mixed with
the live-URL task acceptance or resource profile below.

The original published cohort contained 150 registered Web Fetch task IDs: 100 web
cases and 10 each for document, image, video, challenge and dynamic delivery.
A new cohort takes its participants, task families and counts from its frozen
dataset and current-report profile; it does not inherit the original matrix. Every method
receives the same URL, task rule and end-to-end deadline and is judged by the
shared `score_attempt` path. Implementations are deliberately not forced through
one extractor: the benchmark asks each method to use its fastest documented or
built-in route to the task's final deliverable. Web and document-body tasks end
in readable text; direct-file tasks end in verified original bytes.

## Runtime and route

| Method | Deployment | Task-native route |
|---|---|---|
| Kitesurf | Cloudflare account Browser Run | page text: `/markdown?browser=kitesurf`; resource URLs: Kitesurf CDP DOM reads; document text: native `/markdown`; original files: the Kitesurf CDP endpoint |
| Lightpanda | local browser | page text: `fetch --dump markdown --json`; original files and shared DOM projections: native CDP |
| Obscura | local browser, standard mode | page text: pinned upstream Markdown expression with the final URL; original files: `fetch --dump original`; synchronous DOM projections: `--eval`; asynchronous embedded documents: native CDP |
| Moli | local CLI | page text: native Markdown; final URL comes only from unambiguous same-call official diagnostics; original inline files: native CDP response bytes; attachment events: the same pinned Moli CLI raw response; resource URLs, media metadata and embedded documents: shared DOM expressions |
| Chromium | local Playwright/CDP | page text: rendered DOM → `node-html-markdown`; original files: navigation response or native download; resource URLs, metadata and embedded documents: shared DOM expressions |

Choose the route from the input URL and declared deliverable before calling the
browser. Ordinary, dynamic, popular-site and risk-control text remain page tasks.
Image/video cases distinguish resource URLs, original file bytes and page metadata;
a Markdown response cannot substitute for a required original file.

文档正文必须由所选浏览器的原生内容接口返回。直接 PDF/DOCX 使用各自原生正文
接口；不支持时保存真实失败，不交给外部 pypdf、DOCX XML 或 OCR 补正文。
嵌入文档页面可通过该浏览器的 CDP 或脚本接口读取已经加载的 PDF 查看器文字，
或页面可见文字；不得额外下载嵌入 PDF 后解析。Chromium 的例外只覆盖网页 HTML
到 Markdown 的标准转换。

图片与视频原始文件保留浏览器响应或下载路线。Moli 附件事件可调用同一固定二进制
的原始附件接口，仍受同一截止时间约束；原文件字节不会被外部转换成正文成绩。
资源地址读取已经加载的 DOM 与同源子页面，不额外抓取模块源码挖掘链接。
最终网址只作辅助信息；正文验收以目标内容、必需字段和拦截页规则为准。

Local HTTP downloading and Workers AI conversion are separate methods, not
Kitesurf results. Every Kitesurf Quick Action and CDP connection must explicitly
select `browser=kitesurf`; omitting it selects Cloudflare's Chromium backend.
The current report's native-task route gate rejects these substitutions and stale
one-provider projections while retaining genuine native failures.


Chromium and CDP do not expose a native Markdown result. Chromium Reader Mode
produces simplified HTML and does not cover this benchmark's full output
contract. The selected fallback therefore captures the complete rendered DOM
and converts it with the maintained MIT-licensed `node-html-markdown` package.
The measured wrapper also preserves literal preformatted text with a safe fence
and language label; it is not described as an unmodified package default. The
package version, wrapper hash, Node version, source HTML hash and conversion time
are retained per call. Conversion is inside the 45-second deadline.

Kitesurf is not treated as a local open-source engine. As tested, it is a
Cloudflare-hosted preview selected by `browser=kitesurf`; Cloudflare has stated
an intention to open source it, which is not the same as released source code.
The public report must describe the currently available account API, not a
hypothetical local deployment.

The routes have different completion controls because those controls belong to
the tested interfaces: Kitesurf uses `networkidle2`, Chromium uses Playwright
`load` by default, and each CLI uses its own documented completion behavior.
Explicit completion contracts retain their selected navigation condition; Chromium
records `navigation_wait_until` in each call rather than inferring it from the
current adapter. Native execution does not select task-specific readiness hints.
No method receives task answers, task-specific selectors, clicks, scrolling,
personal sessions, stealth fallback or model post-processing. The original
published matrix used one provider call per task and did not retry; later
documented corrections may supersede affected logical results while preserving
the earlier physical evidence.
The shared [retry policy](README.md#retries) applies to every method.

## What is and is not fair

The comparison aligns inputs, outputs, task set, acceptance rules and the
client-observed end-to-end deadline. It does not claim identical internals.
Forcing native Markdown methods through HTML conversion would replace their
product advantage with benchmark code and answer a less useful question.

Kitesurf is remote while the registered local browser configurations run locally. Its latency includes network
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
PDF/DOCX bytes has not delivered document text and fails that body task. A
direct-file case stops at verified bytes and never performs an irrelevant
Markdown conversion. The run does not add an external PDF/DOCX parser, OCR,
media understanding or a generic downloader that bypasses the selected method.

Public report families follow the deliverable rather than the media MIME type.
The frozen `image` and `video` source tracks remain unchanged for provenance,
but the report combines their 16 resource-URL cases and one video-page text
case as **media information from webpages**, and groups their three direct
binary inputs as **original media files**. A webpage resource case succeeds by
returning the declared media URL; it does not need to download that resource.
A direct-file case must return and validate the bytes. This produces seven
reader-facing families without pretending that URL discovery and file delivery
are the same operation.

Moli uses native HTML for image-resource pages. Video-resource pages use its
native JSON page and network record with a single family-wide `done` plus
five-second readiness window, because a hydrated player may request its media
URL without serializing it into an HTML attribute. The shared scorer only reads
URLs already present in that native output; it never derives a playback URL
from a site-specific identifier. This route is selected for the whole video
resource subtype before the call and cannot change after a failure.

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
`npm ci` and the Python benchmark dependencies in a repository-local
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

### Import historical inputs

The historical `evaluation/dataset.json` input is resolved through
`evaluation/source-inputs.json`; its full text and captured sources are outside
Git. Import the approved frozen copy before using the historical commands below,
as described in [reference inputs](../../docs/reference-inputs.md).
`evaluation/dataset-catalog.json` is a public task directory, not an execution
dataset. An explicitly supplied custom dataset remains a normal JSON file.

### Select a cohort and methods

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

### Capture, freeze and check references

Use the certified wrapper below. Existing frozen task and answer bytes remain
unchanged. Only cases declaring `reference_mode: live_exact` need the live
reference lifecycle in [README.md](README.md).

For those live-reference cases, acquisition follows `source_verification.observation_route` (`http`
or `browser`). Existing frozen inputs that name independent visible/rendered
browser DOM acquisition retain that browser requirement. Task identifiers do
not select the acquisition route. A browser-bound reference needs independent
browser observations for both freeze and postcheck; an HTTP response cannot
silently replace them.

Postcheck and per-case certification use the same fresh-reference population
across `web`, `dynamic` and `challenge`, and the case's declared provider cohort.
They wait for missing logical results and reject duplicate or undeclared
providers. Certificate sidecars retain the physical run's records and original
manifest; response artifacts remain in the physical run for assembly. A failed
reference check remains failed when the command resumes.

```sh
uv run --frozen python scripts/run_certified_benchmark.py --dataset DATASET --output RUN --work-root WORK --providers METHODS --case-ids IDS --provider-config PROVIDERS --workers 1 --browser-binary /absolute/path/to/chromium
```

### Certify a new run

An unchanged compiled acceptance-contract dataset is the execution authority.
Freeze its task and rule bytes; do not recover historical qualification files or
repeat admission checks. Live exact-reference cases retain pre-call capture,
freeze and post-call verification under [README.md](README.md). Source
qualification belongs to new tasks or changes to references/acceptance rules.
Use bounded cohorts when a complete serial run would exceed the live-reference
window. The wrapper verifies worker manifests before finalizing the aggregate.

Select the resource tasks through the existing provider-blind selector before
resource calls. Set the dataset comparison kind to
`headless_client_resource_profile`; its provider configuration binds each local
engine, image, executable, 50 ms sample interval, two CPUs and 2 GiB memory.
Run it through the same certified wrapper with one worker, then package:

```sh
make resource-image
uv run --frozen python scripts/run_certified_benchmark.py --dataset RESOURCE/dataset.json --output RESOURCE_RUN --work-root RESOURCE_WORK --providers LOCAL_METHODS --case-ids IDS --provider-config RESOURCE/providers.json --workers 1
uv run --frozen python scripts/package_headless_resources.py --run RESOURCE_RUN
```

The image recipe accepts version-bound build arguments. Set `MOLI_VERSION`
and `MOLI_SHA_ARM64` / `MOLI_SHA_AMD64` together; its executable is installed as
`/usr/local/bin/moli-VERSION`. Lightpanda uses the existing per-architecture
`LIGHTPANDA_URL_*` / `LIGHTPANDA_SHA_*` arguments, and Obscura uses
`OBSCURA_VERSION` with its per-architecture digests. For official Chrome for
Testing, supply `CHROMIUM_URL_ARM64` / `CHROMIUM_SHA_ARM64` or
`CHROMIUM_URL_AMD64` / `CHROMIUM_SHA_AMD64`; a URL without a digest fails the
build. Select the archive's executable under `/opt/chrome-for-testing/` through
`resource_binary`. Omitting these Chrome arguments keeps Debian Chromium.
The recipe defaults preserve the original image configuration; a new cohort
must supply its selected release inputs and verify actual versions and hashes
inside the built image before any measured call.

Independent result review is opt-in for both matrices. Preserve the declared
review policy; start failure review only after an explicit user request, and
review passes or the complete matrix only after a separate full-review request.
Packaging retains the exact reviewed/total scope. Do not use missing independent
review as an execution blocker when the frozen protocol does not require it.

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

The resource cohort uses the four local browser families: Lightpanda, Obscura,
Moli and Chromium. Each new run pins its actual versions and image identity. Kitesurf remains in the correctness and latency report but
is excluded from the resource table because its remote browser CPU, memory, and
upstream network traffic are not observable from the client.

Correct a proven wrong command, implementation version, scorer or reference
by explicitly superseding the affected logical results, rerunning the complete
declared comparison scope and recording the score impact.

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

To assemble certified evidence or retain a declared subset of browsers, use the
same command. Exclusion changes the publication matrix; source calls remain in
the original evidence directory.

```sh
uv run --frozen python scripts/assemble_headless.py --run-dir RUN --certificates CERTIFICATES --output ASSEMBLED
uv run --frozen python scripts/assemble_headless.py --run-dir RUN --certificates CERTIFICATES --exclude-providers METHOD1 METHOD2 --output SUBSET
```

Assembly verifies and copies declared call artifacts and reference observations,
then exposes the new directory only after validation. An existing independent
review can follow unchanged retained attempts; changed evidence needs a separate
review. Historical timing corrections keep their original measurement protocol
and get a derived receipt bound to the retained records. No browser calls or
independent reviews run as part of assembly. Supply `--speed-cohort SPEED` to
apply the same browser exclusion to a packaged same-window speed cohort.

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

For another declared cohort, pass `--canonical-dataset`, `--provider-config`,
`--provenance` and `--report-profile` explicitly. The checker uses those inputs
for task rules, method coverage, executable identities and the report matrix;
its defaults reproduce the historical headless comparison. It verifies saved
outputs without making provider calls or changing verdicts. Explicit diagnostic
evidence remains ineligible for this formal evidence check.

`.python-version`, `uv.lock`, the Makefile and CI pin Python 3.11 and dependency versions.
Formal commands must not fall back to system `python` / `python3`; an unmet runtime contract must fail before execution.

## Release checks

The headless report remains a separately published reference cohort. Its
evidence bundle fixes the dataset ID, task count, provider set, timeout and
route description; `scripts/check_headless_evidence.py` rejects a different
cohort. The repository's current publication profile now points at the
10-method Web Fetch report and must not be inferred from this headless cohort.
Review requirements are bound to the declared evidence scope: unresolved
judgments block publication, and a failure-only review discloses both reviewed
attempts and accepted unreviewed machine passes. Assembly retains inherited
review history only when its scope and judgments match the final results. These checks are
covered by the repository test suite. Fix invalid evidence or implementation
before release.

The preserved public destination for this comparison is the separate app-domain
Sites project `UUAhhQMnrGwm`. The current 10-method report is the separate
project declared by `evaluation/current-report.json`; never use a headless
receipt or evidence bundle to update it.

固定参考答案默认保持冻结。`source_verification.reference_mode: live_exact` 是实时参考核验的显式声明；旧来源记录和缺少新版验收契约均不构成该声明。普通重跑不得据此重新抓取或更新答案。
