# Archived product extraction

Evaluate complete extraction APIs on the 140 archived product pages in
[Zyte's product extraction benchmark](https://github.com/scrapinghub/product-extraction-benchmark).
The dataset commit, label hashes, original scorer and extruct baseline are pinned in
[`zyte-product-extraction.json`](../../src/harness/benchmarks/cards/zyte-product-extraction.json).
Page bytes, labels and upstream code are downloaded separately with their notices.

## Prepare and reproduce

```sh
uv sync --frozen --extra web-scraper-eval --extra test
uv run --frozen python -m harness.web_scraper.cli prepare
uv run --frozen python -m harness.web_scraper.cli reproduce
```

The default data directory is `~/.browser-eval/web-scraper/`, configurable through
`BROWSER_EVAL_DATA_HOME`. Evidence, report content and report-instance profiles stay
outside Git. Runtime API credentials come from the repository `.env`; variable names
are documented in `.env.example`. Never include credentials in a profile or report.

Reproduction scores the upstream saved predictions. New provider runs use the same
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
| `browserbase-stagehand` | Hosted Stagehand Extract | Frozen HTTPS URL; provider-hosted browser and inference |
| `extruct` | Upstream local baseline | Archived HTML |

Browser infrastructure requiring a separately connected model is outside this
comparison. Stagehand uses its native Model Gateway; it does not read an OpenAI
key. Bright Data's site-specific scraper remains discoverable as a candidate, but
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
  oxylabs-json zyte-product diffbot-product browserbase-stagehand extruct \
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

`probe` checks three stock/field strata; `probe --largest-page` checks the largest
input. These are compatibility diagnostics, not a substitute for a 140-page run.
The lower-level `run` command also collects the full population, but formal cohorts
must go through `scripts/run_certified_benchmark.py`.

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
binds dataset provenance and optional authored content hashes. The current research
renderer accepts compatibility diagnostics and rejects full-quality cohorts;
certified full runs can already be scored and inspected in their saved evidence.

The separate release check requires a receipt from browser checks covering the
core route classes, generated languages, themes and viewport sizes:

```sh
uv run --frozen python -m harness.web_scraper.cli check /absolute/report \
  --release --visual-receipt /absolute/private/visual-receipt.json
```

Building a local report does not upload it or change access permissions.
