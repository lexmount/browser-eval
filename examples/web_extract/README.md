# Web Extract: structured field extraction

Evaluate complete extraction APIs on the 140 archived product pages in
[Zyte's product extraction benchmark](https://github.com/scrapinghub/product-extraction-benchmark).
Frozen inputs are distributed through
[Product extraction benchmark (Zyte)](https://huggingface.co/datasets/Lexmount/LexBench-Zyte-Product-Extraction).
The shared [data lock](../../src/harness/benchmarks/data-lock.json) selects its full
commit and archive/file hashes. The upstream commit, original scorer and extruct baseline are pinned in
[`zyte-product-extraction.json`](../../src/harness/benchmarks/cards/zyte-product-extraction.json).
The dataset contains all original compressed HTML and labels, separate source-qualified
annotations and their correction ledger, and the unchanged upstream evaluator.
Scores and provider outputs are kept outside the dataset. Preparation uses the
shared cache, works offline after download, and preserves existing hash-bound inputs.

## Scope and naming

Web Extract measures structured field extraction from archived HTML: the caller
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
| `extruct` | Upstream local baseline | Archived HTML |

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
checks this one manifest rather than downloading individual pages. The result
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

## Freeze and run the full population

```sh
uv run --frozen python -m harness.web_extract.cli plan \
  --methods cloudflare-json firecrawl-json scrapegraph-extract scrapfly-product \
  oxylabs-json zyte-product diffbot-product browserbase-json \
  --snapshots /absolute/data/snapshots.json \
  --output /absolute/data/full-cohort.json
uv run --frozen python scripts/run_certified_benchmark.py --track web-extract \
  --profile /absolute/data/full-cohort.json --method firecrawl-json \
  --run-dir /absolute/data/runs/firecrawl
uv run --frozen python -m harness.web_extract.cli score /absolute/data/runs/firecrawl
```

The recommended comparison includes complete native APIs only. Upstream extruct
code and earlier baseline predictions remain in the original distribution for
reproduction; the baseline is excluded from new comparisons.

Run each participant once across the complete 140-case population. The plan binds
cases, input hashes, pacing and retry policy before calls. Calls within a provider
are serial; Diffbot additionally waits after each response. A quota or permission
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
[public input distribution](https://huggingface.co/datasets/Lexmount/LexBench-SWDE/tree/1a53107d865085d00458301abb8077dd01a2e651)
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
  plan --methods zyte-custom firecrawl-json scrapegraph-extract \
  cloudflare-json browserbase-json oxylabs-json \
  --snapshots /absolute/data/snapshots.json \
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

The primary score reproduces MarkupLM’s page-hit precision, recall and F1
arithmetic: a matching value makes a page-field hit even when other values are
missing or extra. The subset fixes all 320 site-field groups in the macro
population, including groups with no present values in the sample. This subset
adaptation is not a reproduction of a full SWDE leaderboard. Value-level micro
precision/recall/F1 and exact field-set rate expose partial lists and extra values.
Schema validity is separate from field quality. Every completed API call retains
latency and any native usage receipt; quota denials pause collection.

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
uv run --frozen python -m harness.web_extract.cli check /tmp/extract-example
uv run --frozen python -m pytest tests/web_extract -q
```

The runnable example uses three synthetic cases, including successful extraction
and blocked access. It downloads nothing and needs no credentials. Chinese is the
default; English navigation shows a not-generated notice with a return link.
`--language en` explicitly generates both editions with matching inventories.

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

Each product or record case has one comparison page. Participant links open the
matching detail panel through its URL fragment. Each panel keeps its own field
diagnostics, failure evidence and response dialog. Adding participants expands
these pages without creating a separate HTML file for every method and case.

Exercise this mode with the same renderer:

```sh
uv run --frozen python -m harness.web_extract.example \
  --output /tmp/extract-cohort-example --mode full-cohort-progress
```

The separate release check requires a receipt from one desktop reader pass over
each top-level navigation page in each generated language, plus the English notice
when applicable. Responsive layout, themes and detail interactions belong to
renderer/component checks when those owners change, rather than every release:

```sh
uv run --frozen python -m harness.web_extract.cli check /absolute/report \
  --release --visual-receipt /absolute/private/visual-receipt.json
```

Building a local report does not upload it or change access permissions.

Keep the deployed site, expected remote version, profile hash and report-manifest
hash in an external `publication-target.json` beside the report evidence. Select
that binding explicitly for publication; never borrow a target from another report.
Publication rejects a different profile before uploading. An approved profile
revision records `approved_profile_successor` in that target with the exact
`from_sha256`, `to_sha256` and the verified `profile_path`; a successful update
consumes this approval and records the new profile path and hash together.

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

The same report owner supports registered record schemas through
`mode: field-extraction-cohort`. The instance content binds every planned
participant to its certified source or journal checkpoint, with source-plan
hashes, pricing and a frozen preview origin. Complete certified methods expose
page-hit macro F1, value-level micro F1 and exact-field-set rate. Incomplete
methods retain progress and latency without a quality score.

```sh
uv run --frozen python -m harness.web_extract.example \
  --output /tmp/extract-record-example --mode field-extraction-cohort
uv run --frozen python -m harness.web_extract.cli check /tmp/extract-record-example
```

The synthetic example has no provider certification and cannot be released.
The report inventories the entire planned matrix, including unexecuted pages,
and links category/site/field summaries to per-result filters, exact values,
frozen inputs and lazy native evidence. Public projections bind original and
public hashes separately and must reproduce every aggregate metric. The formal
release gate requires all participants complete and a hash-bound visual receipt.

An explicit report-scope omission sets `include_in_comparison: false` and supplies
`exclusion_reason` on that participant. Its calls, progress, detail panels and
timings remain available, while quality and speed leadership use only the declared
comparison participants. Release checks require every compared participant to be
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


Shared record instructions are frozen in the run contract and appended to each
field's schema description, including APIs that accept only property definitions.
The request receipt retains the effective schema, instruction text and any standalone prompt. Earlier
cohorts retain their actual requests; a new adapter cannot change their results.
A repair inherits its parent's instructions and native options unless the plan explicitly
overrides the native options. The instruction-delivery format is frozen too: historical
record runs with a different format require a new cohort instead of a mixed repair. Bare run IDs resolve under `<home>/runs/`; explicit relative paths
resolve from `<home>`, and absolute paths retain their meaning.

## Self-hosted open-source extraction

`crawl4ai-json`, `scrapegraph-oss-json`, `stagehand-local-json` and `firecrawl-oss-json` use the native
Crawl4AI, ScrapeGraphAI, Stagehand and Firecrawl extractors. They accept the same frozen
Cloudflare page URLs and field schemas as hosted participants. They require a
separately configured model; identify both the library and model in comparisons.
Do not label these configurations as model-free extraction.

The root Dockerfile builds an authenticated service using
`deploy/web-extract/uv.lock`. Set `EXTRACTION_API_KEY`, `EXTRACTION_MODEL`,
`EXTRACTION_MODEL_BASE`, `EXTRACTION_MODEL_API_KEY` and `SNAPSHOT_ORIGINS` on the
service. `SNAPSHOT_ORIGINS` is a comma-separated list of the exact immutable
snapshot origins. The container provides Chromium; `CHROME_PATH` selects its
executable. The service exposes `GET /health` and `POST /extract/{engine}`
(`crawl4ai`, `scrapegraph`, `stagehand` or `firecrawl`). Extraction requires bearer authentication.

For an individual request, set `ENDPOINT`, `SNAPSHOT_URL` and `EXTRACTION_API_KEY`
in your shell. This example uses the identity returned by the running service:

```sh
curl --fail --silent --show-error "$ENDPOINT/health" > health.json
jq --arg url "$SNAPSHOT_URL" '{
  url: $url,
  schema: {type: "object", properties: {name: {type: "string"}}, required: ["name"]},
  expected_runtime: .engines.crawl4ai
}' health.json > request.json
curl --fail --silent --show-error "$ENDPOINT/extract/crawl4ai" \
  -H "Authorization: Bearer $EXTRACTION_API_KEY" --json @request.json
```

Save the health response before calls. Pass a per-method options object to
`plan --native-options /absolute/native-options.json`:

```json
{
  "crawl4ai-json": {
    "endpoint": "https://your-extraction-service.example",
    "runtime_identity": {"engine": "crawl4ai", "version": "FROM_HEALTH", "model": "FROM_HEALTH", "model_base": "FROM_HEALTH", "implementation_sha256": "FROM_HEALTH", "environment_sha256": "FROM_HEALTH"}
  }
}
```

Copy the complete identity object from `health.engines.crawl4ai`, including any
additional fields, rather than using the placeholders above. Each request carries
that identity; a changed model, dependency environment or worker implementation
is rejected. Use the normal certified runner and scorer. Keep the API key in the
caller's repository `.env`, never in the options file. An extraction process has a
110-second deadline within the track's existing 120-second request budget. Timed-out
workers and their browser descendants are terminated and reaped.

Set `EXTRACTION_MODEL_CONTEXT_TOKENS` to the configured model's documented context
limit. For Firecrawl, configure `FIRECRAWL_BACKEND_URL` as its private service URL
and `FIRECRAWL_IMAGE` as its immutable image digest. Mount the packaged
`firecrawl_backend.mjs` on the backend and start it with Node. It launches the
unchanged native server and exposes a private `/identity` endpoint on port 3020;
set `FIRECRAWL_IDENTITY_URL` to that endpoint. The identity binds the backend's
actual bundle, dependencies, Node executable and model configuration. A changed
backend is rejected even when the facade itself has not changed. The backend keeps its own
native extraction; the authenticated facade forwards its JSON result and receipt.
Keep the database, queue, browser and backend API internal.
