# Archived product extraction

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

## Prepare and reproduce

```sh
uv sync --frozen --extra web-scraper-eval --extra test
uv run --frozen python -m harness.benchmarks.data web-scraper
uv run --frozen python -m harness.web_scraper.cli prepare
uv run --frozen python -m harness.web_scraper.cli reproduce
```

The default data directory is `~/.browser-eval/web-scraper/`, configurable through
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

Browser infrastructure requiring a separately connected model is outside this
comparison. Browserbase JSON uses only the Browserbase API key. The separately
registered Stagehand route uses its native Model Gateway and needs a project ID;
it does not read an OpenAI key. Bright Data's site-specific scraper remains discoverable as a candidate, but
is rejected by the full archived-page plan because it cannot parse arbitrary HTML.

URL-only interfaces need a reachable HTTPS address serving the frozen snapshots.
Start the fixture origin with `serve --port 8767`, then expose that origin through
an HTTPS reverse proxy. Only the registered HTML paths are served. Gzip transport
preserves the original decompressed bytes. The collector checks every selected
remote file against its frozen hash before and after collection. Original live
websites are never substituted for archived inputs. External images and scripts
are not archived, so browser rendering can still fetch those resources.

## Freeze and run the full population

```sh
uv run --frozen python -m harness.web_scraper.cli plan \
  --methods cloudflare-json firecrawl-json scrapegraph-extract scrapfly-product \
  oxylabs-json zyte-product diffbot-product browserbase-json extruct \
  --output /absolute/data/full-cohort.json
uv run --frozen python scripts/run_certified_benchmark.py --track web-scraper \
  --profile /absolute/data/full-cohort.json --method firecrawl-json \
  --run-dir /absolute/data/runs/firecrawl \
  --fixture-base https://your-frozen-origin.example
uv run --frozen python -m harness.web_scraper.cli score /absolute/data/runs/firecrawl
```

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

## Research and compatibility reports

```sh
uv run --frozen python -m harness.web_scraper.example --output /tmp/scraper-example
uv run --frozen python -m harness.web_scraper.cli check /tmp/scraper-example
uv run --frozen python -m pytest tests/web_scraper -q
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

Exercise this mode with the same renderer:

```sh
uv run --frozen python -m harness.web_scraper.example \
  --output /tmp/scraper-cohort-example --mode full-cohort-progress
```

The separate release check requires a receipt from one desktop reader pass over
each top-level navigation page in each generated language, plus the English notice
when applicable. Responsive layout, themes and detail interactions belong to
renderer/component checks when those owners change, rather than every release:

```sh
uv run --frozen python -m harness.web_scraper.cli check /absolute/report \
  --release --visual-receipt /absolute/private/visual-receipt.json
```

Building a local report does not upload it or change access permissions.

Keep the deployed site, expected remote version, profile hash and report-manifest
hash in an external `publication-target.json` beside the report evidence. Select
that binding explicitly for publication; never borrow a target from another report.

## Failure repairs and field audit

`repair-plan --content CONTENT --options OPTIONS --fixture-base URL --output PLAN`
freezes the exact failed method/case scope. Optional `--cases CASES` selects a
subset of current failures. Run the plan through the certified entry above with
the affected method and a fresh directory. Each completed call is retained on
resume. Successful parent calls are never selected. Native request options are
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

The report home exposes all-call mean/P50/P95 and sourced pricing for every method.
Quality cells link to method/field filters; result rows open lazy evidence dialogs
and link to full field diagnostics, same-case comparisons and safe source previews.
Optional `source_screenshots` binds author-provided images by source path/hash;
their capture timing is disclosed separately from frozen HTML evidence.

## Publish an existing report

Keep the report target and version outside Git. The adapter uses the shared staged
publication owner and retains the target's access policy.

```sh
uv run --frozen python -m harness.web_scraper.cli publish \
  --source /absolute/report --target /absolute/publication-target.json \
  --work /absolute/new-publication --visual-receipt /absolute/visual-receipt.json
```
