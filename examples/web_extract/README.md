# Web Extract: structured field extraction

Evaluate complete extraction APIs on the 140 archived product pages in
[Zyte's product extraction benchmark](https://github.com/scrapinghub/product-extraction-benchmark).
Frozen inputs are distributed through
[Product extraction benchmark (Zyte)](https://huggingface.co/datasets/Lexmount/LexBench-Zyte-Product-Extraction).
The shared [data lock](../../src/harness/benchmarks/data-lock.json) selects its full
commit and archive/file hashes. The upstream commit and original scorer are pinned in
[`zyte-product-extraction.json`](../../src/harness/benchmarks/cards/zyte-product-extraction.json).
The dataset contains all original compressed HTML and labels, separate source-qualified
annotations and their correction ledger, and the unchanged upstream evaluator.
Scores and provider outputs are kept outside the dataset. Preparation uses the
shared cache, works offline after download, and preserves existing hash-bound inputs.

## Scope and naming

Web Extract measures structured field extraction from archived pages or live URLs: the caller
requests fields such as price, stock or author, and the service returns field values.
Web scraping is the broader term for collecting website data; extraction is the
part measured here. [Zyte calls it automatic extraction](https://docs.zyte.com/zyte-api/usage/extract/index.html).
[Firecrawl calls it structured extraction](https://github.com/firecrawl/firecrawl/blob/main/apps/js-sdk/firecrawl/README.md).
[Browserbase exposes schema-based JSON through Fetch](https://www.browserbase.com/blog/introducing-authenticated-fetch).

Vendor products keep their official names, such as
[Bright Data Web Scraper API](https://github.com/brightdata/skills/blob/main/skills/bright-data-best-practices/references/web-scraper-api.md).
Web Extract names this benchmark track. Source URLs, SDK names and quoted text
remain unchanged.

The maintained module is `harness.web_extract`, the certified track is
`--track web-extract`, and the optional dependency group is `web-extract-eval`.
Existing evidence remains at its original location; pass `--home` to use it.
The data-lock key, generated formats, report labels and examples all use
`web-extract`. Original source identifiers remain in the
[migration provenance](../../src/harness/benchmarks/code-migration.json), locked
historical editions and third-party citations. Archived inputs stay readable;
new files use the canonical format names. No legacy command alias is registered.

## Prepare and reproduce

```sh
uv sync --frozen --extra web-extract-eval --extra test
uv run --frozen python -m harness.benchmarks.data web-extract
uv run --frozen python -m harness.web_extract.cli prepare
uv run --frozen python -m harness.web_extract.cli reproduce
```

The default data directory is `~/.browser-eval/web-extract/`, configurable through
`BROWSER_EVAL_DATA_HOME`. Evidence, report content and report-instance profiles stay
outside Git. Runtime API credentials come from the repository `.env`; variable names
are documented in `.env.example`. Never include credentials in a profile or report.

Reproduction downloads the three hash-pinned upstream saved predictions separately
when absent. They are historical comparison evidence, not public dataset inputs.
New provider runs use the same
price, SKU and stock labels and original evaluator. Price and SKU require the
original scorer's exact normalized values. Missing output contributes to recall;
HTTP success alone does not establish extraction success. Bootstrap dispersion is
reported as the upstream standard deviation, not a 95% confidence interval.
Historical predictions are separate from new measurements.

## Participants and inputs

| Method ID | Complete interface | Input |
| --- | --- | --- |
| `cloudflare-json` | Browser Rendering JSON | Uploaded HTML and JSON Schema |
| `firecrawl-json` | Scrape JSON | Frozen HTTPS URL and JSON Schema |
| `scrapegraph-extract` | Hosted Extract | Uploaded HTML, instruction and JSON Schema |
| `scrapfly-product` | Product Extraction | Raw uploaded HTML |
| `oxylabs-json` | Web API JSON | Frozen HTTPS URL and JSON Schema |
| `zyte-product` | Product extraction, `userHtml` | Uploaded HTML with original URL as context |
| `diffbot-product` | Product API POST | Raw uploaded HTML with original URL as context |
| `browserbase-json` | Native JSON extraction | Frozen HTTPS URL and JSON Schema; built-in extraction |

The hosted API cohort uses each provider's built-in extraction. Browserbase JSON
uses only the Browserbase API key. The separately registered hosted Stagehand route
uses its Model Gateway and needs a project ID; it does not read an OpenAI key.
Self-hosted configurations use a separate model and are described below. Bright
Data's site-specific extractor is rejected by the full archived-page plan because
it cannot parse arbitrary archived HTML.

New cohorts give every native API the same public URL for each frozen page.
Each dataset has its own hosting service. Browser Eval can find and reuse a
Cloudflare deployment (the default) or verify a service hosted elsewhere:

```sh
# Check HF latest and find a matching deployment in your Cloudflare account.
uv run --frozen --extra web-extract-eval python -m harness.web_extract.cli \
  --home /absolute/data/DATASET host \
  --output /absolute/data/snapshots.json

# Alternatively, use your own public service.
uv run --frozen --extra web-extract-eval python -m harness.web_extract.cli \
  --home /absolute/data/DATASET host --provider external \
  --base https://your-service.example/frozen \
  --output /absolute/data/snapshots.json
```

Cloudflare reads `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` from the
repository `.env`. Colleagues with access to the same account and Pages projects
find the same deployment. Read permission is enough to reuse one; creating or
updating a deployment also needs Pages write permission. Registered project names
are resolved through the dataset registry; `--project` overrides that selection.
At entry, the host command resolves HF latest to a full commit and compares its
original HTML inventory with the prepared dataset. Use `--revision HF_COMMIT_OR_TAG`
to select another HF edition; its full resolved commit is frozen in the result.
Discovery searches historical Cloudflare deployments as well as the current one.
Each managed snapshot has one public `snapshots.json` declaring the HF dataset
repository, full source commit and complete HTML/file inventory hashes. Discovery
uses this manifest to identify the dataset version. On first use, it also hashes
every served page and resource. The verified deployment ID and whole-input hash
are cached under the configured data home; later runs reuse that proof only after
Cloudflare confirms the same immutable deployment. Concurrent first users share
one audit, and static downloads reuse network connections. The result
records both the selected HF commit and the snapshot's source commit; an identical
HTML inventory allows reuse across metadata-only HF revisions. Snapshots without
this declaration require an approved new deployment. Matching inputs are
reused without uploads or provider calls. If hosting is missing or differs, the
command writes a deployment proposal and makes no remote changes. After approval,
run the same command with `--approve-plan /absolute/data/approved-proposal.json`
and a fresh output path. A changed HF commit or Cloudflare deployment invalidates
the proposal before any write. The deployment is checked before it can be used.
A new HTML version gets a new immutable deployment URL. Earlier cohorts retain
their original URLs and input protocols. External services require no Cloudflare
credentials. Their first binding checks every page against the frozen HTML, even
when they expose a matching manifest; the manifest alone is a claim by that host.

Use the generated binding in `plan --snapshots`. URL-capable interfaces receive
the same case URL; upload-only interfaces receive the identical frozen HTML and
can share the plan. A plan containing only upload-only methods can omit the binding. Before and after calls, managed Cloudflare snapshots use
the frozen whole-dataset manifest and its hash; a changed manifest blocks certification.
External hosts additionally have every selected page hash-checked before and after calls.
An unconfirmed Cloudflare binding follows the same byte checks. The management API
must confirm the immutable deployment identity, and a complete byte-verification
proof must bind that deployment to the selected input inventory, before a
whole-manifest check can replace HTML and resource downloads. A new deployment
or changed inventory requires another byte check.
Page-by-page verification remains an explicit byte-audit command and is not used
to identify a managed snapshot version.
Keep raw HTML, original benchmark names, sources and licenses; exclude reference
answers, API responses and credentials from public hosting. New Cloudflare
packages block scripts and external resources through response headers; earlier
deployments retain their recorded serving behavior.

Hugging Face keeps the authoritative input files. An optional `snapshots.json`
records the public URLs and matching hashes without replacing those files.
`snapshots-publish` compares the pinned HF originals with a complete online
verification receipt, then atomically adds the binding and dataset-card section.
A mismatch prevents publication. Use a full HF commit when downloading a binding;
a later HTML change requires a new deployment and a new HF commit. Running
cohorts do not follow moving aliases or auto-update their inputs.

The [snapshot index](https://lexbench-snapshots.pages.dev) is an optional navigation
page. Dataset discovery and evaluation do not depend on it. `snapshots-build`,
`snapshots-verify` and `snapshots-index` expose the package, verification and
navigation stages for other hosting workflows.

## Compare model backends

For an existing GPU host, use a separate Ollama installation and model store when
the installed service cannot load the selected model. Take `OLLAMA_RELEASE`,
`OLLAMA_ARCHIVE_SHA256` and exact model tags from the frozen experiment inputs.
The following Linux setup leaves the existing service and its models in place:

```sh
MODEL_ROOT="${MODEL_ROOT:?Set a directory on the model data volume}"
mkdir -p "$MODEL_ROOT/runtime" "$MODEL_ROOT/models"
curl --fail --location \
  "https://github.com/ollama/ollama/releases/download/${OLLAMA_RELEASE:?}/ollama-linux-amd64.tar.zst" \
  --output "$MODEL_ROOT/runtime.tar.zst"
printf '%s  %s\n' "${OLLAMA_ARCHIVE_SHA256:?}" "$MODEL_ROOT/runtime.tar.zst" | sha256sum --check
tar --zstd --extract --file "$MODEL_ROOT/runtime.tar.zst" --directory "$MODEL_ROOT/runtime"
OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MODELS="$MODEL_ROOT/models" \
  OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_CONTEXT_LENGTH=20000 \
  OLLAMA_LOAD_TIMEOUT="${MODEL_LOAD_TIMEOUT:-5m}" \
  OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_NO_CLOUD=1 \
  "$MODEL_ROOT/runtime/bin/ollama" serve
```

In another terminal, pull each declared tag with
`OLLAMA_HOST=127.0.0.1:11435 "$MODEL_ROOT/runtime/bin/ollama" pull "$MODEL_TAG"`.
Keep the listener private. For a long collection, give the SSH forward an owned
control socket so it outlives the invoking terminal or tool session:

```sh
MODEL_SSH_SOCKET="${MODEL_SSH_SOCKET:?Set an absolute path for this run's control socket}"
ssh -fN -M -S "$MODEL_SSH_SOCKET" -o ControlPersist=8h \
  -o BatchMode=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -L 19435:127.0.0.1:11435 "$MODEL_SSH_TARGET"
ssh -S "$MODEL_SSH_SOCKET" -O check "$MODEL_SSH_TARGET"
```

After collection, close that forward with
`ssh -S "$MODEL_SSH_SOCKET" -O exit "$MODEL_SSH_TARGET"`.
Freeze `/api/version` and
`/api/tags` identities before calls, and check actual GPU layer placement in the
server log. Existing workloads remain part of the declared host conditions.

Use `model-compare` for a model-only streaming diagnostic. It supplies the same
frozen page text, instruction and JSON Schema to each configuration. Reference
answers stay outside the request. These results describe model extraction from
the supplied text; they do not replace native extractor scores.

The external plan declares cases, text hashes, model parameters, ordered physical
calls, deadlines, execution resources and a wall-time budget. Controlled Ollama
configurations also bind the server version and installed model digest. Read
credentials from the repository `.env` through each model's `key_env`.
Auxiliary HTTP requests have total wall deadlines: 10 seconds per runtime/weight
verification request, 180 seconds per model load, and 5 seconds per telemetry
request. A model's `auxiliary_timeouts` mapping may override `verification`, `load`
and `telemetry` with finite positive seconds; these settings remain bound during
recovery. Measure cold loading on the selected model volume before freezing those
limits. A slow disk may require both a longer `auxiliary_timeouts.load` and a
matching `MODEL_LOAD_TIMEOUT`; these preparation limits do not extend the model's
generation deadline. Telemetry expiry retains the completed inference and records its own
elapsed time. Error responses retain a redacted body of at most 16 KiB so quota
and account-permission denials stop admission.

Freeze sampling parameters from each model's official guidance before collection;
a shared temperature of zero can cause repetitive generation on some models.
Declare the common time and output-token budgets separately from these native
settings. Diagnose repetition or exhausted output budgets before interpreting
latency as a hardware limitation. Preserve any corrected configuration's earlier
calls and replace its complete declared scope rather than retrying selected cases.

```sh
uv run --frozen python -m harness.web_extract.cli model-compare \
  --plan /absolute/data/model-plan.json --output /absolute/data/model-run --phase preflight
uv run --frozen python -m harness.web_extract.cli model-compare \
  --plan /absolute/data/model-plan.json --output /absolute/data/model-run --phase comparison
uv run --frozen python -m harness.web_extract.cli --home /absolute/data/zyte model-score \
  --plan /absolute/data/model-plan.json --run /absolute/data/model-run \
  --contract /absolute/data/case-contract.json --stage validation \
  --output /absolute/data/model-scores.json
```

Preflight records initialization separately and binds the shared execution-budget
assessment. A malformed answer remains a quality failure; it is not a readiness
exclusion. Every call retains elapsed time and raw stream events. First output,
first answer and successful JSON completion each have their own observed counts;
failed requests retain elapsed time without a JSON-completion milestone.
A completed success or failure is reused on resume.
To extend a completed model study, freeze only the additional configurations in a
new plan and run directory. Reuse the original case contract and bind the existing
baseline plan, call index and scores by hash; do not include baseline calls in the
new plan. Predeclare thinking modes and sampling parameters before collection.
Show historical and supplemental collection windows beside their latency metrics.
Matched frozen inputs preserve the quality comparison. A contemporaneous speed
comparison needs a fresh, authorized baseline cohort. Preserve the existing full
SDK/API cohort when adding model-only evidence.
An unresolved in-flight call stops collection for reconciliation. Changed inputs,
weights, server versions or collector code require a new frozen contract.
Offline scoring verifies the complete selected case matrix, original request
bindings and retained response hashes. It uses the track's existing field rules,
keeps unavailable reference fields outside quality denominators, and reports
format validity separately from field accuracy. A validation partition remains
separate from the screening set used to choose configurations.
The synthetic scenarios in
[`test_model_comparison.py`](../../tests/web_extract/test_model_comparison.py)
exercise the request format, real streams, deadlines and independent resource lanes.

The model diagnostic reports value-level micro F1 over annotated fields, using
the same upstream field matchers as the native track. It is separate from the
SDK/API report's per-field product scores and official SWDE page-hit F1. Its
hash-bound contract preserves the held-out page text, reference exclusions and
reference revision; rebuilding a report never substitutes today's dataset or
fetches live pages. Model streams use the shared scheduler with protocol-specific
prepare/commit callbacks, not the native SDK row or certification format.

A connection outage stops admission after retaining the failed call. Resume does
not silently repeat it. For a proven connection refusal or interrupted chunked
stream, freeze recovery before making new calls. Run the repair's comparison
phase only when its call list contains comparison entries; a preflight-only repair
ends after its preflight command. Then continue the original phases before scoring:

```sh
uv run --frozen python -m harness.web_extract.cli model-repair-plan \
  --plan /absolute/data/model-plan.json --run /absolute/data/model-run \
  --output /absolute/data/delivery-repair-plan.json
uv run --frozen python -m harness.web_extract.cli model-compare \
  --plan /absolute/data/delivery-repair-plan.json --output /absolute/data/delivery-repair \
  --phase preflight
# Skip this command for a preflight-only repair.
uv run --frozen python -m harness.web_extract.cli model-compare \
  --plan /absolute/data/delivery-repair-plan.json --output /absolute/data/delivery-repair \
  --phase comparison
uv run --frozen python -m harness.web_extract.cli model-compare \
  --plan /absolute/data/model-plan.json --output /absolute/data/model-run --phase preflight \
  --repair-plan /absolute/data/delivery-repair-plan.json --repair-run /absolute/data/delivery-repair
uv run --frozen python -m harness.web_extract.cli model-compare \
  --plan /absolute/data/model-plan.json --output /absolute/data/model-run --phase comparison \
  --repair-plan /absolute/data/delivery-repair-plan.json --repair-run /absolute/data/delivery-repair
uv run --frozen python -m harness.web_extract.cli --home /absolute/data/zyte model-score \
  --plan /absolute/data/model-plan.json --run /absolute/data/model-run \
  --contract /absolute/data/case-contract.json --stage validation \
  --repair-plan /absolute/data/delivery-repair-plan.json \
  --repair-run /absolute/data/delivery-repair --output /absolute/data/recovered-scores.json
```

Recovery retains the original run and binds every predecessor hash, request,
configuration, timeout, execution policy and replacement. Scoring requires the
complete declared recovery, uses its final response even if it fails, and records
predecessor-plus-final execution time separately from final-call latency.
Unavailable timing remains unavailable. Completed answers, malformed JSON and
exhausted generation budgets are ineligible. A connection timeout stops collection
but needs delivery reconciliation before any new contract.

For later failures on other calls, repeat both repair flags in frozen order on
planning, collection and scoring. Earlier final responses remain fixed; each
logical result has at most one transport recovery.

## Freeze and run the full population

```sh
uv run --frozen python -m harness.web_extract.cli plan \
  --methods firecrawl-json \
  --concurrency 4 --execution-purpose quality \
  --snapshots /absolute/data/snapshots.json \
  --output /absolute/data/candidate.json
uv run --frozen python -m harness.web_extract.cli preflight \
  --profile /absolute/data/candidate.json --method firecrawl-json \
  --run-dir /absolute/data/preflight/firecrawl \
  --assessment-output /absolute/data/firecrawl-execution.json
uv run --frozen python -m harness.web_extract.cli plan \
  --methods firecrawl-json --concurrency 4 --execution-purpose quality \
  --snapshots /absolute/data/snapshots.json \
  --execution-assessment /absolute/data/firecrawl-execution.json \
  --output /absolute/data/full-cohort.json
uv run --frozen python scripts/run_certified_benchmark.py --track web-extract \
  --profile /absolute/data/full-cohort.json --method firecrawl-json \
  --run-dir /absolute/data/runs/firecrawl
uv run --frozen python -m harness.web_extract.cli score /absolute/data/runs/firecrawl
```

The comparison includes complete native extraction APIs.

The candidate plan fixes the sample's configuration. `preflight` selects frozen
cases across domains, uses the same caller and scheduler as the full run, and
keeps its calls outside the measured cohort. New CLI plans require the bound
assessment before certified collection. A multi-method plan accepts repeated
`--execution-assessment` paths; each participant needs its own matching sample.
Reuse an assessment only when its input, route, runtime and execution policy match.

Run each participant once across the complete 140-case population. The plan binds
cases, input hashes, pacing, concurrency and retry policy before calls. New CLI
plans default to four in-flight calls per provider. A provider-mandated post-call
cooldown selects one in-flight call and records the reason. Choose capacities
from a measured preflight using the shared [execution contract](../../docs/benchmark-execution.md).
Self-hosted methods share their endpoint's frozen `service_worker_capacity`,
which caps the requested concurrency. A service-capacity 429 stops collection
as an infrastructure rejection rather than becoming a scored engine failure.
For isolated timing, select `--execution-purpose latency --concurrency 1` and give
`--serial-reason`. Quality-run timings describe the declared concurrent load.
A quota or permission
denial pauses collection. After access is repaired, repeat the same certified
command and directory: completed cases are retained, the blocked case may be retried
once, and both physical responses remain inspectable. The final response is scored;
there is no best-response selection or quality retry. The certificate is emitted
only after the complete scope and reference checks pass.

A native-field mapping correction can be scored from retained responses with
`score RUN --reproject --output /absolute/data/scores-revision.json`. This keeps
collected fields and original scores intact, runs paired stock-label controls,
and binds the reason, regression IDs, projection source hash and changed case IDs
in the separate assessment. No provider calls are made. Report selections must
bind that assessment's path and hash explicitly.

Native input preflights use the internal collector exercised by the fixture tests.
Only the certified entry point starts a publishable provider cohort. A changed
implementation cannot resume an unfinished run; finish it on its original commit.
Completed certificates remain readable without invoking the old runner.

## SWDE multi-domain extraction

The same preparation, certified runner and scorer support SWDE. The pinned
[SWDE card](../../src/harness/benchmarks/cards/swde.json) selects ten pages per
website across 80 websites and eight domains before any provider calls. This is
an 800-page subset of the original 124,291-page collection. Its
[public input distribution, LexBench-SWDE-800](https://huggingface.co/datasets/Lexmount/LexBench-SWDE-800/tree/1a53107d865085d00458301abb8077dd01a2e651)
contains unchanged HTML, original paths, checksums and source/license notices;
reference answers and provider outputs are excluded. The original project
license is Ms-RL; captured website content retains its publishers’ rights.

Preparation takes the original archive identified by the card and uses `bsdtar`
with 7-Zip support. On first use it downloads the evaluator from the pinned
Microsoft source URL and verifies its SHA-256 before unpacking any pages. Later
preparation can use the cached evaluator offline. An interrupted compilation
leaves the final dataset directory absent, so the same command can be retried.
Original annotations and evaluator stay in the local evidence directory.
Use a separate data directory for this benchmark:

```sh
uv run --frozen --extra web-extract-eval python -m harness.web_extract.cli --home /absolute/data/swde \
  prepare --benchmark swde --source /absolute/downloads/swde-source.tar.gz
uv run --frozen --extra web-extract-eval python -m harness.web_extract.cli --home /absolute/data/swde \
  plan --methods scrapegraph-extract \
  --concurrency 4 --execution-purpose quality \
  --snapshots /absolute/data/snapshots.json \
  --output /absolute/data/swde/candidate.json
uv run --frozen --extra web-extract-eval python -m harness.web_extract.cli --home /absolute/data/swde \
  preflight --profile /absolute/data/swde/candidate.json --method scrapegraph-extract \
  --run-dir /absolute/data/swde/preflight/scrapegraph \
  --assessment-output /absolute/data/swde/scrapegraph-execution.json
uv run --frozen --extra web-extract-eval python -m harness.web_extract.cli --home /absolute/data/swde \
  plan --methods scrapegraph-extract --concurrency 4 --execution-purpose quality \
  --snapshots /absolute/data/snapshots.json \
  --execution-assessment /absolute/data/swde/scrapegraph-execution.json \
  --output /absolute/data/swde/precall.json
uv run --frozen --extra web-extract-eval python scripts/run_certified_benchmark.py --track web-extract \
  --home /absolute/data/swde --profile /absolute/data/swde/precall.json \
  --method scrapegraph-extract --run-dir /absolute/data/swde/runs/scrapegraph
```

Every method receives the domain’s array-valued field schema. URL participants
receive the same public snapshot URL. Zyte Custom extracts from its fetched HTTP response;
Firecrawl Scrape JSON, ScrapeGraph Extract, Cloudflare JSON, Browserbase JSON and
Oxylabs JSON use their bundled extraction APIs. The plan binds the versioned URL
and rejects an override at collection. Product-specific extractors are rejected
for SWDE. Firecrawl Parse uses HTML upload; it can share a plan with URL-based
methods, retaining its own input route and the same frozen HTML bytes.

The [original SWDE paper, Section 7.1.2](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/StructedDataExtraction_SIGIR2011.pdf)
defines page-hit precision, recall and F-score. The primary score reproduces MarkupLM’s page-hit precision, recall and F1
arithmetic: a matching value makes a page-field hit even when other values are
missing or extra. The subset fixes all 320 site-field groups in the macro
population, including groups with no present values in the sample. This subset
adaptation is not a reproduction of a full SWDE leaderboard. Value-level micro
precision/recall/F1 and exact field-set rate expose partial lists and extra values.
Schema validity is separate from field quality. Every completed API call retains
latency and any native usage receipt; quota denials pause collection.

Both product and array-field reports also compute whole-page exact accuracy and
empty-field accuracy from the retained final responses. Whole-page accuracy
requires a successful response, usable field types and every requested field
correct; pages with incomplete reference coverage are excluded from its denominator.
Empty-field accuracy uses covered page-fields whose reference is empty. Failed
calls remain incorrect, and array tasks must explicitly return an empty array.
These diagnostics do not change the upstream scores or require provider calls.
The report includes numerator/denominator counts, per-page outcomes and the input
binding in `data/completion-metrics.json`; its release check recomputes the metrics
and validates the overview and interpretation tables against the same evidence.
The core table keeps these columns beside the existing field metrics. Its metric
guide identifies their project-defined origin and gives formulas, population rules
and worked examples; upstream metrics retain their own source explanations.

Source-qualified reference corrections use the same scoring entry point:

```sh
uv run --frozen python -m harness.web_extract.cli --home /absolute/data/swde \
  score /absolute/data/swde/runs/METHOD \
  --reference-revision /absolute/data/swde/reference-revision.json \
  --output /absolute/data/swde/source-qualified-scores.json
```

The revision binds the original reference, every frozen HTML file, executable
regression controls and each changed answer's source element. Encoded labels are
decoded only when the source text proves the change; literal entity text stays
literal. Truncated labels need an explicit field-context decision. Excluding an
unannotated site-field group requires a bound coverage receipt and a check of its
complete original annotation file, including pages outside the selected sample.
Additional source alternatives, placeholder removal and confirmations of disputed
labels use `field_decisions`: each binds the original values, accepted values,
source hash, benchmark rule and complete text-node evidence with field context.
Every accepted value and every removed original value needs evidence. Attribute
values and concatenations of separate text nodes cannot stand in for text nodes.
Unrelated answer edits, changed sources and populated groups are rejected.
Scoring retains native answers and original scores, adds no provider calls, and
records the correction authority, causes and regression IDs separately. It does
not declare independent review complete.

This adapter currently supports certified collection and scoring. The product
report and product reference-review interfaces require price/SKU/stock fields
and are not used for SWDE’s domain-specific fields.

## Research and compatibility reports

```sh
uv run --frozen python -m harness.web_extract.example --output /tmp/extract-example
uv run --frozen python -m harness.web_extract.cli --home /tmp/extract-example-synthetic-inputs \
  check /tmp/extract-example --profile /tmp/extract-example-synthetic-inputs/profile.json
uv run --frozen python -m pytest tests/web_extract -q
```

The runnable example uses three synthetic cases, including successful extraction
and blocked access. It downloads nothing and needs no credentials. Chinese is the
default; English navigation shows a not-generated notice with a return link.
`--language en` explicitly generates both editions with matching inventories.

Authored HTML intros use `pages.<route>.intro` with `en` and `zh` values. Cohort
home pages place the intro after the core comparison and its reference note;
other pages start with it. Formal checks reproduce the complete intro inventory
from the profile-bound content through the renderer, then verify its HTML, URL
transformations and position alongside the core-table order.

For a research report, pass `report --profile /absolute/report-profile.json
--content /absolute/content.json --output /absolute/new-report`. The external profile
binds dataset provenance and optional authored content hashes. The `full-cohort-progress` model binds a predeclared cohort plan and one source
per participant. It validates certificates for complete cohorts and immutable
journal entries for paused cohorts. Quality is shown only for complete certified
methods; the result inventory includes every planned method and case, with
unexecuted cases labeled separately. Credential-redacted evidence views are grouped
by method and loaded on demand, with original/public hashes and unchanged extracted
fields. Combining independently frozen source plans requires an explicit
`source_cohorts` method-to-plan-hash binding; each run retains its own timing policy.
This progress report does not certify an incomplete comparison.

Use `pages.<route>.intro` on non-home routes for language-keyed HTML that must precede generated
tables, such as a pipeline diagram at the start of an interpretation page. Keep
the remaining authored material in `body`. The manifest binds the intro's
rendered content and position; checks reject a removed, changed or moved intro.
English builds require an English intro whenever a Chinese intro is supplied.
Home pages keep their core comparison first and place authored `body` content afterward.

Model diagnostics use `model_comparison.dataset` and `model_comparison.tables` in
the bound content. Each stage (`screening` or `validation`) selects cohorts with
an `archive` path/hash and a `scores` member path/hash. Optional per-model labels
and cohort labels are language keyed. Put one matching `data-model-screening`
or `data-model-validation` placeholder on the interpretation page. The maintained
renderer generates these tables from the retained scoring output, using the same
aggregation owner as `model-score`; build and release checks recompute case counts,
field F1, schema validity, whole-page correctness and timing distributions. Tables
with missing bindings or edited values fail release. Model-study timings collected
in separate windows remain neutral descriptive measurements.

The plan CLI defaults to one in-flight call per method. Use explicit
`--concurrency` for quality or throughput collection. Historical calls retain their
original concurrency; reports suppress latency winners when effective limits
differ or are undeclared. Resource caps apply before comparing those limits.

Each product or record case has one comparison page. Participant links open the
matching detail panel through its URL fragment. Each panel keeps its own field
diagnostics, failure evidence and response dialog. Adding participants expands
these pages without creating a separate HTML file for every method and case.

Exercise this mode with the same renderer:

```sh
uv run --frozen python -m harness.web_extract.example \
  --output /tmp/extract-cohort-example --mode full-cohort-progress
```

The release check requires a receipt from one desktop reader pass over
each top-level navigation page in each generated language, plus the English notice
when applicable. Responsive layout, themes and detail interactions belong to
renderer/component checks when those owners change, rather than every release.
Use the standalone check for local validation without publishing:

```sh
uv run --frozen python -m harness.web_extract.cli --home /absolute/benchmark-home \
  check /absolute/report --release --profile /absolute/private/profile.json \
  --visual-receipt /absolute/private/visual-receipt.json
```

Checks, packaging and publication require the external frozen profile and
an explicit `--home`. They verify the pinned dataset, frozen content, selected
plans, certificates and assessments. They reproduce the public audit view and
bind each cached field judgment and native response to its original audit. Public manifest edits cannot
remove the required audit or change the completion denominator. Pass the same
`--profile` to `package`; publication selects it from the bound target. Use the
same global `--home` as the report build so original calls and repair chains resolve
against the correct benchmark inputs.

Building a local report does not upload it or change access permissions.
The `publish` command runs the release check and packages the report itself;
it does not require a preceding standalone release check or package command.
It reports elapsed time for package preparation, remote-version preparation and
deployed-file verification. Resuming the same work directory reuses verified
staged files. Keep the final export comparison: it proves that the deployed file
tree matches the validated package.

Keep the deployed site, expected remote version, profile hash and report-manifest
hash in an external `publication-target.json` beside the report evidence. Select
that binding explicitly for publication; never borrow a target from another report.
Publication rejects a different profile before uploading. An approved profile
revision records `approved_profile_successor` in that target with the exact
`from_sha256`, `to_sha256` and the verified `profile_path`; a successful update
consumes this approval and records the new profile path and hash together.

Archived report builds validate their saved snapshot receipts offline. Publication
rechecks the hosted snapshot. Set `content.path` in the external publication target
to the report's content file; its bytes must match the built report's content hash.
For a suite, that file selects each child's content and snapshot receipt. Live-only
reports need no snapshot audit or additional content binding.

## Failure repairs and field audit

Select one parent run per method. Each parent must contain one method; duplicate
method entries are rejected before a repair plan is written.

`repair-plan --content CONTENT --options OPTIONS --output PLAN` freezes the exact failed method/case scope. Optional `--cases CASES` selects a
subset of current failures. The snapshot origin is inherited from the selected
failed execution; `--fixture-base URL` is optional for older inputs without a bound
origin. Upload-only repairs require no URL. `run --fixture-base` remains available
for frozen historical plans that did not bind an origin; it cannot override a
versioned snapshot. Run the plan through the certified entry above with
the affected method and a fresh directory. Each completed call is retained on
resume. Successful parent calls are never selected. New calls retain their access-stop
reason before checkpointing. Explicit account denials pause immediately; ambiguous
HTTP 403s pause after consecutive denials across three source sites. A successful
call or another status resets that streak. Historical retry verification uses the
recorded decision when present and a fixed legacy eligibility rule otherwise;
it never applies the evolving live classifier to old calls. Native request options are
validated before dispatch; source HTML and URL fixtures retain their hashes.

A report selection's `repair` list binds each repair directory and certificate
SHA-256 in order. Aggregation preserves all physical calls, the final response,
retry execution time and missing historical timers. It rejects modified source
snapshots, receipts, calls or parent certificates. This is a failure-only repair,
not a fresh uniform-settings cohort; comparisons retain that limitation.

`review --content CONTENT --work WORK --model MODEL` creates resumable source and
native-response audit packets, with literal source citations and complete field
coverage. `--prior COMPLETED_AUDIT` reviews changed repair results only while
reusing unchanged judgments. Model evidence audits are labeled separately from
human annotations. Reference changes require source-grounded adjudication and
paired scorer controls, not model agreement alone.

Record audits can join disjoint complete review scopes, such as separately run
providers, through the same assembly command:

```sh
uv run --frozen python -m harness.web_extract.cli --home /absolute/data/swde \
  audit-assemble --profile /absolute/data/report-profile.json \
  --content /absolute/data/report-content.json \
  --initial /absolute/data/review-first-providers \
  --additional-review /absolute/data/review-last-provider \
  --adjudication /absolute/data/reference-adjudication \
  --reference-revision /absolute/data/reference-revision.json \
  --output /absolute/data/complete-audit.json
```

Repeat `--additional-review` and `--additional-adjudication` for non-overlapping
scopes. Assembly checks every native execution, source, field schema and reference;
it rejects missing or duplicate results and projections changed after review.
When a retained repair's execution identity changes, use `review --prior` and
`audit-assemble --supplemental` for exactly that changed scope. The original review
remains bound, and assembly verifies the supplement against the current native
response, runtime error and execution identity. Unchanged results cannot enter the
supplement and are not reviewed again.
Disputed references remain unresolved until source adjudication and any required
reference correction agree. Complete result coverage alone does not clear those
disputes. Assembly adds no provider calls and preserves reviewer findings beside
the scores produced by the frozen text-node contract.
An explicit source decision can settle an adjudicator disagreement after source
adjudication; the original finding remains in the audit alongside that decision.
Profiles requiring full review reject missing or unresolved audits at report build
and release. Reports retain original annotations and scores next to the qualified
comparison, with native outputs and timings unchanged.

The report home exposes all-call mean/P50/P95 and sourced pricing for every method.

When total charges are unavailable, a price entry can declare `cost_unavailable`
with `population_cases`, `missing_cases`, localized `reason`, and an `evidence`
path/SHA-256 binding. Materialize that JSON under `assets/` using the existing
asset registry. The disclosure uses schema `web-extract-cost-disclosure/1`, the
method ID, `case_count`, `total_charge_receipts` and `source_plan_sha256`.
Build and release checks require matching populations and source-plan hashes;
an unavailable total cannot also declare a numeric price or billing formula.

Quality cells link to method/field filters; result rows open lazy evidence dialogs
and link to full field diagnostics, same-case comparisons and safe source previews.
Optional `source_screenshots` binds author-provided images by source path/hash;
their capture timing is disclosed separately from frozen HTML evidence.

## Publish an existing report

Keep the report target and version outside Git. The adapter uses the shared staged
publication owner and retains the target's access policy.

```sh
uv run --frozen python -m harness.web_extract.cli publish \
  --source /absolute/report --target /absolute/publication-target.json \
  --work /absolute/new-publication --visual-receipt /absolute/visual-receipt.json
```


## Snapshot hosting cost and capacity

Cloudflare Pages static-file requests are free and unlimited; these snapshot
packages do not use Functions. The documented Free-plan limits are 100 Pages
projects per account, 20,000 files per deployment and 25 MiB per file. The Pages
build service has 500 builds per month on Free; this is a build quota, not a limit
on reads or a count of retained snapshot versions. Browser Eval uploads prepared
files with Wrangler. One dataset uses one project and can have multiple immutable
deployments. The current 140-page and 800-page editions fit these limits.

The official limits page does not state a numeric total for retained deployment
versions. Do not describe retention as unlimited or treat CDN cache expiry as
snapshot deletion. Keep the HF originals and validate the selected deployment
before use. Larger datasets can use another hosting service through `--provider
external --base URL`; provider-specific billing and lifecycle remain with that
service.

Verified 2026-11-04 against [Pages pricing](https://developers.cloudflare.com/pages/functions/pricing/),
[Pages limits](https://developers.cloudflare.com/pages/platform/limits/),
[Direct Upload limits](https://developers.cloudflare.com/pages/get-started/direct-upload/#limits)
and [versioned preview URLs](https://developers.cloudflare.com/pages/configuration/preview-deployments/#preview-aliases).

## Multi-value field reports

### Source-qualified external cohorts

Use the `records` adapter for another benchmark with explicit field schemas and
source-validated references. Its definition stays outside Git and binds the source
versions, sampling rules, page hashes, schemas and reference hashes. References are
used for local scoring and never sent to an extraction API.

A definition may set `extraction_instructions` to declare its task scope. This
text is frozen in the dataset profile, plan and request contract. List-query
benchmarks should request all matching records; the default single-record
instructions remain available for detail-page benchmarks. Field descriptions
carry the original question when an API accepts a schema without a separate query.

```sh
uv run --frozen --extra web-extract-eval python -m harness.web_extract.cli \
  --home /absolute/data/cohort prepare --benchmark records \
  --source /absolute/data/cohort-definition.json
```

The definition declares `input_mode: snapshot` or `input_mode: live`:

| Mode | URL sent to the native API | Source validation |
| --- | --- | --- |
| `snapshot` | The immutable hosted page URL selected by `host` | HF revision and the complete HTML/resource inventory before and after collection |
| `live` | The original website URL in the task | Original reference values checked on the source before and after collection |

Live cohorts reject snapshot bindings. Saved HTML is private source evidence;
providers still visit the original site. Unavailable pages or references that no
longer appear stop certification, while completed provider calls remain saved.
An optional `live_reference_policy.maximum_observation_age_seconds` (at most
86,400 seconds) permits a previously captured independent source when a fresh
access attempt fails. Each case then freezes `qualification.source_url` and a
timezone-aware `qualification.observed_at`. Preserve a header-only charset in
`qualification.encoding`, such as `utf-8`; it is bound with the captured bytes
and used when checking them. Preparation freezes the effective charset. Declared
charsets take precedence; undeclared valid UTF-8 stays UTF-8, with the HTML parser's
legacy detection as a fallback. Reference checks and HTML uploads use this same rule.
A successfully observed page with unsupported
reference values still stops certification. Access failure alone does not
establish that the question or answer is invalid.
Live cohorts resume through their original certified run. The archived repair
entry point rejects live inputs so a retry cannot substitute a hosted page or
HTML upload for the original URL. Explicit HTTP-200 challenge pages count as
access failures and follow the same bounded capture policy; a changed source
page with missing answers still fails reference qualification.

Source checks run at most four origins concurrently, with serial requests and a
one-second pause within each origin. They do not contribute to provider latency.
Transient network errors and HTTP 408/429/5xx responses allow up to three source
checks, with bounded backoff recorded in the receipt. Missing reference values do
not trigger retries. This policy never retries provider extraction for a better score.

Snapshots can include captured styles, images and other resources. Their hashes
are part of the whole-snapshot version; changing an asset changes that version
even when the main HTML is unchanged. Scripts and external resources remain
blocked by response headers.

The shared scorer reports page-hit macro F1, value-level micro F1 and exact-field
set rate. Text matching preserves content and units while normalizing whitespace.
Explicitly typed URL fields resolve relative links against the source URL, using
the same matching rule in aggregate scores and result details. A cohort using
these rules must disclose them as an adaptation unless they reproduce the
upstream evaluator.

Source-qualified record datasets pin a portable `evaluate.py`. Dataset validation
requires its bytes to match the runtime evaluator. A scorer change needs a declared
scoring correction, or reproduction at the original code revision; it cannot
silently change a frozen dataset's scoring behavior.

The same report owner supports registered record schemas through
`mode: field-extraction-cohort`. The instance content binds every planned
participant to its certified source or journal checkpoint, with source-plan
hashes, pricing and a frozen preview origin. Complete certified methods expose
page-hit macro F1, value-level micro F1 and exact-field-set rate. Incomplete
methods retain progress and latency without a quality score.

```sh
uv run --frozen python -m harness.web_extract.example \
  --output /tmp/extract-record-example --mode field-extraction-cohort
uv run --frozen python -m harness.web_extract.cli --home /tmp/extract-record-example-synthetic-inputs \
  check /tmp/extract-record-example --profile /tmp/extract-record-example-synthetic-inputs/profile.json
```

The synthetic example has no provider certification and cannot be released.
The report inventories the entire planned matrix, including unexecuted pages,
and links category/site/field summaries to per-result filters, exact values,
frozen inputs and lazy native evidence. Public projections bind original and
public hashes separately and must reproduce every aggregate metric. The formal
release gate requires all participants complete and a hash-bound visual receipt.

An explicit report-scope omission sets `include_in_comparison: false` and supplies
`exclusion_reason` on that participant. Its calls, progress, detail panels and
timings remain available, while quality uses only the declared comparison
participants. Timing and price tables retain every planned participant; timing
extrema use the stated all-call population, including account rejections.
Release checks require every compared participant to be
complete and reject an omitted participant without a reason. Source execution
plans and certificates are never rewritten to change this presentation scope.

## Verified source previews in record reports

A record report binds its preview to the same whole-dataset snapshot as collection.
Set `snapshot_base` to the immutable dataset URL and `preview_snapshot` to the
path and SHA-256 of the saved `host` output:

```json
{
  "snapshot_base": "https://DEPLOYMENT.pages.dev/DATASET/HTML_INVENTORY_SHA256",
  "preview_snapshot": {
    "path": "/absolute/snapshot-binding.json",
    "sha256": "SHA256_OF_SNAPSHOT_BINDING_FILE"
  }
}
```

The build rechecks the remote dataset manifest against the retained receipt and
local HTML inventory. A different dataset version or origin stops the build.
The report retains the verified public receipt, without Cloudflare account details.
The release check also verifies the preview and download URLs on every source page.

For repaired product calls, `adjudicate --additional-prior /absolute/supplemental-review`
includes new reference disputes alongside the original review. Assembly requires
adjudication of either review's disputes and rejects unrelated predecessor reviews.

Product and record cohorts can combine disjoint participant reviews through
`audit-assemble --additional-review`. To reuse completed reference adjudications,
pass `adjudicate --resolved-adjudication /absolute/completed-adjudication`.
Only identical status-and-value proposals are skipped; new claims still require
adjudication. The retained packet and review hashes protect both forms of reuse.
`adjudicate --case-ids /absolute/ready-cases.json` freezes a subset whose review is
complete, so source adjudication can overlap the remaining result review. Use
disjoint subsets of the new review; final assembly still requires full coverage.


Shared record instructions are frozen in the run contract and appended to each
field's schema description, including APIs that accept only property definitions.
The request receipt retains the effective schema, instruction text and any standalone prompt. Earlier
cohorts retain their actual requests; a new adapter cannot change their results.
A repair inherits its parent's instructions and native options unless the plan explicitly
overrides the native options. The instruction-delivery format is frozen too: historical
record runs with a different format require a new cohort instead of a mixed repair. Bare run IDs resolve under `<home>/runs/`; explicit relative paths
resolve from `<home>`, and absolute paths retain their meaning.

## Self-hosted open-source extraction

The complete [deployment and reproduction guide](../../deploy/web-extract/README.md)
includes the six-service Compose stack, Rainbond mapping, four-route smoke check,
both dataset commands, credential names and Firecrawl's commercial-use conditions.

### Candidate qualification

Qualification asks whether the project's native interface accepts a page plus
field meanings/schema and returns field values without site-specific selectors.
A built-in HTTP server is optional: this project wraps native libraries in the
same authenticated API. Qualification is distinct from the complete quality run.

| Candidate | Native extraction path | Qualification and comparison scope |
| --- | --- | --- |
| Crawl4AI 0.9.5 | `LLMExtractionStrategy`, native XML-block protocol | Complete Zyte 140 and SWDE 800 cohorts. |
| ScrapeGraphAI 2.3.1 | `SmartScraperGraph` with frozen HTML and schema | Complete cohorts. HTML input avoids the verified URL preprocessing defect. |
| Stagehand 4.4.0 (Python) | `Stagehand.extract` over the browser's accessible content | Complete cohorts. |
| Firecrawl, digest-pinned server | `/v2/scrape` with JSON format and schema | Complete cohorts; backend and its dependencies are separately deployed. |
| Scrapy 2.19.0 | CSS/XPath selectors in a spider | Frozen-page fetch/title selection succeeded. Semantic fields need a supplied selector set or additional extractor. Suitable for a rule/agent-generation comparison; not an independent schema-driven extractor in this cohort. |
| Scrapling 0.5.1 | Selectors, adaptive matching, fetchers and MCP tools | Frozen-page parsing/title selection succeeded. Its MCP integration supplies tools to an external agent; the selected library does not itself define this cohort's semantic extraction policy. A fixed agent/extractor would form a new configuration. |
| Crawlee 3.18.2 | Cheerio/Playwright handlers; optional `StagehandCrawler` | Basic fetch/title extraction succeeded. The Stagehand integration also returned schema fields after selecting its Responses protocol. It is eligible as a Crawlee + Stagehand combination, but was not run as a fifth independent extraction method: its extractor is Stagehand. Its JavaScript 3.7.3 configuration is distinct from the Python participant; their scores are not interchangeable. |

The report method pages link the actual single-page qualification records,
the failed Chat protocol attempt, the successful Responses attempt, and the
checked upstream source commits. Scrapy/Scrapling selector results establish
parsing capability, not cross-site semantic accuracy. Crawlee's single-page result
establishes compatibility, not complete Zyte/SWDE coverage. Only the four complete
certified configurations receive benchmark scores.

`crawl4ai-json`, `scrapegraph-oss-json`, `stagehand-local-json` and `firecrawl-oss-json` use the native
Crawl4AI, ScrapeGraphAI, Stagehand and Firecrawl extractors. They accept the same frozen
Cloudflare page URLs and field schemas as hosted participants. They require a
separately configured model; identify both the library and model in comparisons.
Do not label these configurations as model-free extraction.

Crawl4AI uses the SDK's default XML-block output protocol. Its schema prompt asks
for XML-wrapped records; forcing JSON mode can retain a `blocks`/`score` envelope
instead of the requested fields. The runtime identity binds this protocol. Run
`make test-extraction-sdk` to exercise the pinned SDK parser with deterministic
model responses, without browser navigation or model API calls.

The ScrapeGraphAI adapter fetches the immutable snapshot bytes and passes them to
the native graph's HTML input. Its receipt retains the fetched HTML hash. This
avoids the 2.3.1 URL path's incorrect HTML-as-base-URL conversion; no custom field
extractor replaces the graph. The health identity declares `native_input: html`.
All frozen HTML must be UTF-8. Dataset qualification checks every page before any
provider call; the native HTML route also rejects invalid bytes before graph execution.

The [deployment guide](../../deploy/web-extract/README.md) owns service startup,
credentials, runtime identity capture, API examples, timeouts and the Firecrawl
backend topology. Its six-service Compose recipe is the deployment authority;
this guide owns dataset qualification and certified benchmark execution.
