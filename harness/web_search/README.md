# Web Search API evaluation

Browser Eval owns the independent scorer in `lexbench_websearch/`, collection, evidence and reports. [LexBench Web Search on Hugging Face](https://huggingface.co/datasets/Lexmount/lexbench-websearch) owns versioned task definitions and source references. The [data lock](../benchmarks/data-lock.json) selects immutable snapshots. Store instance selectors, execution profiles and publication targets beside external evidence; pass the selector with `--profile`. Historical standalone profiles keep their original bytes.

[LexBench Web Search](https://github.com/lexmount/lexbench-websearch) owns task populations, provenance, reference qualifications and deterministic scoring. Browser Eval imports its pinned package directly. Store the instance selector, execution profiles, publication target and any hash-bound historical evidence authority beside external evidence. Pass the selector with `--profile`; there is no repository default or implicit published report. The collector resolves benchmark references before freezing run inputs. Historical standalone profiles keep their original bytes.

## Run a comparison

Put `LEXMOUNT_SEARCH_API_KEY`, `DEEPSEEK_API_KEY`, and `BOCHA_API_KEY` in the repository `.env`. Obtain a DeepSeek key from [its console](https://platform.deepseek.com/api_keys), a Bocha key from [its platform](https://open.bochaai.com/), and a Lexmount key from the service administrator. Never put credentials in profiles or evidence.

```sh
uv run --frozen python scripts/run_certified_benchmark.py web-search \
  --profile /absolute/instance/current.json \
  --output "$HOME/.browser-eval/web-search/new-run"
uv run --frozen python scripts/build_search_report.py \
  --source "$HOME/.browser-eval/web-search/new-run" \
  --content /absolute/path/content.json \
  --output /absolute/path/report --language zh
```

Use a new output directory for every collection. The frozen profile declares every query, method, result count, timeout, retry rule and price before calls. New cohorts use `protocol.order="complete provider batches"` (the default when omitted): all cases and rounds for one provider finish before the next provider starts. Explicit rotation remains supported only to continue or reproduce frozen historical cohorts. When the profile enables retries, a transport, HTTP or invalid-response failure receives one retry; a valid empty response does not. The final response is the one logical result, while every physical call is retained. A missing credential fails before calls or output creation.

## SERP providers and finite quotas

SERP profiles select providers through the same collector and renderer. Supported adapters include Lexmount, Browserbase Parallel Advanced, Bright Data Google Light JSON and Serper Google. Keep each report's profile, selected participants and publication target alongside its external evidence. These services use different upstream indexes; the comparison measures their delivered rankings.

Configure `LEXMOUNT_SEARCH_API_KEY`, `BROWSERBASE_API_KEY`, `BRIGHTDATA_API_KEY` and `SERPER_API_KEY` in the repository `.env`. Browserbase Search does not require a browser session. Bright Data requires a SERP zone in the profile's `output_options.zone`; its account key alone does not identify the product configuration. Acquire keys from the [Browserbase dashboard](https://www.browserbase.com/overview), [Bright Data console](https://brightdata.com/cp/start), [Serper](https://serper.dev/) and the Lexmount deployment administrator.

The profile requests five results and evaluates the first five native organic entries, preserving their order. Complete native responses remain bound evidence, including any extra results. Lexmount's public configuration selects `output_options.interface=public` and sends only `query` and `limit`; historical profiles retain their original request contract. Source-specific tasks express their domain restrictions through equivalent `site:` operators on the public interfaces. Native titles and snippets stay separate from page bodies and generated answers; a provider's missing snippet remains empty.

Use the certified entry above with an explicit external `--profile /absolute/path/serp-profile.json`. Set rounds and retries to fit the smallest account allowance. `max_calls_per_method` accepts a shared integer or a complete method-to-limit map. It rejects an unaffordable full matrix before network calls and counts retained calls, including resume archives, before further spending. Record earlier configuration probes and the remaining reserve in the profile. Check the remaining balance before another collection.

For a proven native-interface change during an interrupted collection, use `--resume --execution-revision /absolute/revision.json`. The `web-search-execution-revision/1` contract binds the previous and replacement profile hashes, root cause, complete superseded method scope and relative hash-bound evidence files. Only endpoint, native output options and budget metadata may change; tasks and measurement controls stay fixed. The collector preserves every unaffected result, archives displaced calls and their original contract, and recollects the complete changed method. Later resumes retain complete logical calls so another interruption does not spend unaffected providers' quotas again. The receipt distinguishes current physical calls from the total retained historical calls.

Reference responses are captured before and after collection, with immutable hashes. The identity-scoped contract requires complete successful responses containing predeclared publisher or document identifiers. Volatile page chrome may change; missing identities prevent certification. `--diagnostic` preserves the full matrix for investigation and cannot certify a release. Anchor hits test only supplied known-page URLs. Dataset composition and source attribution come from the selected benchmark version.

## Metrics and limits

- Known-page Hit@5 and reciprocal rank use declared anchors. They are not full-web recall or overall answer accuracy.
- Domain fraction tests host boundaries, excluding suffix lookalikes. Empty responses have no domain fraction; list fill and error counts remain visible.
- Duplicate fraction uses conservative URL normalization. Semantic query parameters are preserved.
- Factual probes match published supporting URLs and reference-answer cues. A versioned assessment revision interprets explicit accepted ranges and alternatives from the published answer; historical literal metrics remain reproducible from unchanged collection profiles. Preview fields and all delivered text are evaluated separately. Source matching is not whole-web recall; phrase presence is not semantic correctness. A separate diagnostic excludes explicitly named benchmark mirrors without promoting later results.
- Full scenario relevance uses complete named human annotations, or an explicitly selected frozen model through `scripts/judge_search_relevance.py`. Both produce nDCG@5, Precision@5 and Success@5 through the same scorer. Model inputs omit provider identities and original ranks; retained requests, responses and quoted fields bind every label. Empty and all-zero pools remain in the population. Pass the resulting evidence with `--relevance`; label model judgments clearly.
- Snippet, summary and content lengths count Unicode characters. Text quantity does not establish correctness or source faithfulness.
- Client wall time includes request setup, network transfer, response bytes and any declared retry. Mean, median and nearest-rank P95 share each declared cohort's complete query population. Successful nonempty calls are reported separately. Serial latency does not measure throughput or an SLA.
- Prices remain in their native currencies. Empty DeepSeek successes are free under its published rule. Transport failures have unknown billing. Lexmount cost stays unavailable until pricing is supplied. Estimates are not invoices and exclude downstream models and extra fetches.

Raw response bodies, declared requests, transport errors, timings, cache metadata and source captures stay outside Git. Public result receipts reference raw responses by hash instead of duplicating their full text; checks reconstruct normalized results and verify both their count and content hash. The build verifies the complete matrix, hashes and derived metrics. Both report languages share the same evidence. The performance page separates client duration, reported server duration, transfer time and overlapping upstream attempts; overlapping durations must not be added. Publication uses this track's own target binding and never borrows another report's site. Independent review is opt-in.

## Checks and example

Building validates the frozen evidence and scoring bindings. Use `--check` for a
standalone page check, or run `--release-check` once before publication to validate
pages, evidence and the focused reader receipt together. Scoring-input reuse skips
HTML inspection. Packaging and uploading verify the accepted files rather than
repeating the full report check. Combine `--release-check RECEIPT` with
`--export-delivery DESTINATION` to validate and export in one pass.

API capability research belongs in the main report’s bound `--content` input. The measured report owns interface tables, request specimens and supporting sources.

```sh
uv run --frozen python -m unittest discover -s tests/web_search
uv run --frozen python -m harness.web_search.example --output /tmp/search-example
uv run --frozen python scripts/build_search_report.py --output /tmp/search-example/report --check
```

The example is synthetic and labels its numbers accordingly. Report checks reject broken links, altered evidence, duplicate summaries, missing error details and incorrect winner markers. Before delivery, inspect each generated top-level tab once at the normal desktop layout. Responsive and theme behavior belongs to reusable renderer tests instead of every report release. A local report is not a certified public release.

The separate full gate is `scripts/build_search_report.py --output /absolute/report --release-check /absolute/visual-receipt.json`. It binds the report manifest to one reviewed capture of each top-level tab. Link, evidence, filter and pagination structure is checked during generation. Keep this receipt and screenshots outside Git.

Supplemental cohorts use the same collector with another explicitly selected frozen profile. Present completed cohorts in one report with `--source /absolute/primary-run --cohort social=/absolute/social-run`. Repeat `--cohort NAME=RUN` for additional non-overlapping cohorts. The builder verifies each source independently, rejects duplicate cases and incompatible execution protocols, and renders one query inventory and one result inventory with group filters. The overall table describes saved calls across the fixed query mix; per-cohort timing remains visible and does not become a new same-window measurement. Optional external content supplies bilingual `cohort_labels`. Collection profiles and receipts remain immutable. `--site-prefix` is available when a separately requested nested report is needed. Qualified equivalent-page corrections use `--identity-correction /absolute/path/to/qualified-correction.json`: the report retains the collection profile, original evidence and an explicit before/after scoring receipt. This changes neither calls nor latency. A source observation must prove each alias and the correction must name its cause and regression controls.
Each report has its own external publication authority. It binds the frozen collection profile, report manifest, scoring evidence, archive and exact remote version. Update this binding after a successful upload; preserve the collection profile and use the expected remote version for subsequent updates.


Reader-oriented content can select `reading_model` and supply `reading_sections`
with stable keys, bilingual titles and navigation labels. Sections target those
keys through `page`; `lead` and `summary` surround the measured opening table.
Use `specimens` to select a saved case, provider, result position and text fields
rather than transcribing provider output. Provenance filters are separate from
collection-group filters: an original community query can belong to any batch.
The inventory calls URL anchors “predeclared target pages” because identity
captures are not complete reference answers or relevance judgments.

Supplementary research files use `assets` entries with a relative `source`, public
`path` and `sha256`. Authored source links can set `evidence: true` to open a bound
response in the shared dialog. The same asset path works in both generated
languages. These examples do not enter the measured cohort merely by being
included in the report. Release checks bind the authored explanation, actual
specimens, source-filter populations and chapter visibility to the public build.

For hosts with a file-count limit, export a checked report with
`--output /absolute/report --export-delivery /absolute/new-delivery-directory`.
This preserves the renderer's pages and raw responses in one compressed file per
case, with shared detail and evidence viewers. The export verifies every original
page and response against its source hash. It changes storage and navigation only;
it does not rescore results. Run the full release gate on the canonical report and
exercise the exported viewers on the target host before delivery.

Collection stops after the declared retry if a participant returns HTTP 401 or
402. Resolve the account problem before continuing with the same certified entry
point and `--resume`. Resume requires the unchanged frozen profile and reference
inputs. Complete unaffected cases remain unchanged; all configurations of an
account-failed or incomplete case are collected again. The prior result stream,
displaced responses and implementation snapshot remain hash-bound in the run's
resume archive. A complete receipt is issued only after the entire original
matrix and final reference checks pass. Record the interruption when interpreting
collection windows; the pause is not API latency.

## Three-provider presentation and annotation revisions

External report content selects `presentation: comparison-report`, `comparison_methods`, optional `comparison_labels`, and explicit `metric_populations`. With `independent_benchmarks`, benchmark-root populations partition all cases; auxiliary diagnostic groups may overlap. The builder validates complete original runs before projecting the selected methods. Missing method-query cells fail closed. An `assessment_revision` adds independently qualified reference pages and versions answer matching without new provider calls. It retains original profiles, changed metrics and source observations. Added references must be identified independently of provider outputs; describe their observation date and scope accurately.

The original SimpleQA Verified task measures closed-book model answers using a model grader. This report's source-hit, reciprocal-rank and lexical-cue checks are retrieval adaptations, not official benchmark accuracy. The frozen source version, input-length selection and adapted metrics belong on the dataset and methodology pages.

Results analysis provides a same-query outcome explorer and paginated output columns generated from the method registry. Unknown judgments remain separate from misses. The delivery exporter rewrites dynamic detail links as well as ordinary HTML links, preserving language and exact case evidence. Release acceptance operates filters, pagination and dynamic evidence navigation in addition to the standard success/failure workflows.

## Full in-house relevance annotation

Export the candidate pool from an existing checked report:

```sh
uv run --frozen python scripts/build_search_report.py --output /absolute/report --export-annotations /absolute/new-annotation-directory
```

Open `index.html` in the exported directory. It covers every in-house query and
pools all participants' top-five URLs without displaying provider names or ranks.
Annotators assign 0–3 relevance grades, inspect source pages when needed, and
confirm every query, including queries with no candidates. The page saves progress
in that browser and exports a complete JSON file. It does not call a model or
pre-fill grades.

Pass the completed file to the normal build with `--relevance`. Import verifies
the frozen packet, every query and candidate, and the named annotator. Explicitly
authorized model runs bind exact prompts, requests, responses and quoted evidence.
Sampled in-house label sets are rejected. Fixed labels produce nDCG@5
and Precision@5 deterministically. Duplicate URLs earn no additional credit;
missing slots count toward the five-slot denominator. A query with no relevant
pooled candidate receives zero nDCG and precision and stays in the population.
Known-URL discovery and site constraints remain separate diagnostics.

For explicitly authorized model annotations, run `scripts/judge_search_relevance.py`
with `--report`, `--output`, `--model` and `--base-url`. Supply the credential through
`SEARCH_JUDGE_API_KEY`; do not put credentials in CLI arguments. The runner freezes
its contract, retains each response, resumes completed queries and refuses changed
inputs. Import its `relevance.json` through the existing report `--relevance` option.
Model annotations are labeled separately from human annotations. The rubric uses
0–3 grades, with nDCG@5, Precision@5 (grade ≥2), and Success@5 (at least one grade 3).

Site-family domain lists and explicit native-source routes are mandatory judge constraints. Domain lists on
ordinary searches remain source diagnostics; explicit constraints in the query
still apply under the common rubric.
Bind official-source alternatives with `--source-requirements` when judging a
report, or embed them in a frozen packet. The program checks URL eligibility;
the model receives titles, snippets and eligibility flags without source URLs.
Ineligible candidates score zero while their original model grades remain in
the evidence. A changed frozen rubric requires an explicit zero-search-call
`relevance_revision` in report content; build and check verify its input bindings.
Retrieval reports also use supporting-source hits as the default factual check
in same-task comparisons. Answer text remains an optional diagnostic.

When correcting source requirements, preserve the original relevance evidence
and pass it to the judge with `--reuse-evidence /absolute/relevance.json`.
The runner inherits calls whose judge requests are unchanged and judges only
changed requests. Report content records `relevance_revision.source_rule_correction`
with schema `web-search-source-rule-correction/1`, a `root_cause`, `provider_calls: 0`,
and the paired `regression_ids` listed by `SOURCE_RULE_REGRESSIONS` in the relevance
module. Set `before_relevance_sha256` to the SHA256 of the predecessor serialized
with `json.dumps(predecessor, ensure_ascii=False, sort_keys=True).encode()`.
`changed_case_ids` lists exactly the queries with changed candidate eligibility
flags; `new_judge_case_ids` lists the current plan minus `inherited_case_ids`.
Metadata changes that leave judge inputs unchanged reuse their original calls.
Build and check verify the predecessor, original physical receipts, case lists
and regression controls before accepting the correction.


## Independent result audit

Independent audit is a separate, explicitly requested stage. Freeze the requested scope before reviewers see prior scores:

```sh
uv run --frozen python scripts/review_search_results.py freeze \
  --report /absolute/report --output /absolute/new-review --model MODEL_ID
```

Freeze requires an explicit model ID. Failure-only review is the default; a full
review requires `--scope all --authorization` with the user authorization record.
Run frozen packets through the maintained CLI backend and collect its completed
results:

```sh
uv run --frozen python scripts/review_search_results.py run --review /absolute/review --workers 4
uv run --frozen python scripts/review_search_results.py collect --review /absolute/review --require-complete
```

An unavailable model or failed job earns no coverage. Successful packets are
reused on resume, and changed inputs are rejected. Execution records are
operator-attested and hash-bound; they are not third-party signed attestations.

Coverage counts questions, method outputs and pooled relevance candidates
separately. Every selected output, including empties and errors, requires a review
record. Unreviewed successful outputs are counted explicitly. A factual reference
can be accepted only with an independent frozen source excerpt; tested provider
outputs cannot substitute for it.
Long document checks cover delivered structure and the query-relevant passages
in complete saved bodies; review coverage is not a claim that every unrelated
sentence in every returned document has been fact-checked. Provider limitations,
dataset defects and scoring defects remain distinct findings. Completed review
and successful provider output are different states; findings require explicit
disposition before the final audited report is released.

A formal reviewed release passes `--review /absolute/review-evidence.json` to
`build_search_report.py`. It must contain all opinions in the declared scope and
an adjudication for every dataset issue, output issue and finding. Each decision
binds the original opinion hash, its resolution, reason and evidence. Provider
limitations are recorded separately from corrected evaluation defects. The build
rejects incomplete declared coverage, changed reviewer opinions, unmatched outputs and open
findings; the release check also verifies rendered summary and detail evidence.

A source-rule correction can pass `--reuse-evidence /absolute/relevance.json` to
the judge entry point. It revalidates the predecessor and reuses a call only when
its complete request is unchanged. Changed inputs receive new physical receipts;
the resulting evidence retains the predecessor and identifies every inherited
case. Transport or model configuration changes cannot silently reuse old calls.

A completed independent reference review may quarantine a defective factual query
from every participant’s quality denominator. The original task, response and
metrics remain inspectable; all-call latency and cost retain the entire cohort.
Each exclusion must bind a dataset finding and an explicit adjudication.

### Comparison report reading layout

The comparison home owns the compact quality/latency/price summary and same-scope
page/site discovery comparison. Detailed relevance and same-query specimens belong
to the quality page; collection-specific timing, server diagnosis and billing share
one performance page. The dataset page owns current inputs and provenance; the result
explorer shows one query per output page without truncating full summaries or dropping
providers. Methodology and independent review remain linked appendices. Redundant
standalone cost and interpretation routes are not generated in this layout.

## Publish a replacement

`scripts/publish_search_report.py` freezes the checked report, source receipts,
content, visual evidence and lossless delivery archive before any upload. Pass
`--report`, `--visual-receipt`, `--content`, each `--source COHORT=RUN` and a new
`--output` directory inside `BROWSER_EVAL_DATA_HOME`. The next release is checked
against its own frozen inputs while the existing site identity and remote version
remain locked. `--resume` reuses the same plan and idempotent upload operation.
The command records the activated version in this track’s publication target.

Review CLI jobs use the signed-in Codex account in an isolated configuration,
copying only its authentication file. Search-provider and model-judge API keys
continue to come from the repository `.env`; they are not passed to review jobs.

Publication also requires `--profile /absolute/instance/current.json` for a new plan. The plan pins that selector; resume reuses it and rejects changed inputs. Publishing updates only the selected external target receipt. New report builds with legacy evidence must explicitly bind `assessment_authority` by relative path and SHA-256 in the selector. The checked report carries that authority as a hash-bound asset; no report-specific exception list is compiled into the package.

Interface text is maintained in `messages.json`. Source-language policy checks reject inline Chinese in the Search renderers and report-instance schemas in `evaluation/`, including renamed files. Synthetic examples and benchmark task definitions remain versioned.
