# Web Fetch

## Main and full content

An annotated task may declare `content_scopes.version: 1` with `main` and
`full` profiles. Each profile contains a complete `requirements` object and
`evidence` with a source SHA-256 and a description of its reference scope.
Main content retains complete body regions and core records; full content may
also require page controls, surrounding text and comments. Media and original
file delivery remain task requirements in either scope.

Use `--content-scope main|full` with `lexbench-webfetch score`, or the Web Fetch
`run`, `rescore` and `validate` commands. Selection happens before execution or
scoring. Explicit selection fails for tasks without both annotations. Legacy
tasks keep their existing behavior when no scope is selected. Scores bind the
selected profile hash; reuse saved responses for score-only comparisons.

Public definitions retain both profiles and reference hashes. Export and score
the matching approved frozen inputs, which contain the bound reference bodies;
public reference hashes alone are not executable body references.

Browser Eval owns this track's execution, evidence and report pipeline, and the independent scorer in `src/lexbench_webfetch/`. Public task definitions and reference bindings are versioned in [LexBench-Web-Fetch on Hugging Face](https://huggingface.co/datasets/Lexmount/LexBench-Web-Fetch). See [benchmark integration](../../../docs/benchmarks.md) for input export and dataset selection.

Fetch a page or file, score the returned content against declared requirements, and inspect the saved result. Start with the [local article example](../../../examples/web_fetch/README.md) for a runnable HTTP/Moli/Chromium comparison.

This page is the technical reference for larger experiments and historical reports. Use the section matching your task:

| I want to… | Read |
| --- | --- |
| Run local browsers or reproduce the live-URL comparison | [Browser comparison guide](HEADLESS.md) |
| Compare extraction from frozen HTML | [WebMainBench guide](WEBMAINBENCH.md) |
| Understand task pass rules and text quality | [Text evaluation](#common-text-evaluation) · [stable acceptance](#stable-acceptance-and-text-quality) |
| Capture an independent reference | [Source capture](#independent-source-capture) · [browser observations](#independent-browser-observations) |
| Define dynamic-page completion | [Dynamic-content protocol](#dynamic-content-ground-truth-protocol) |
| Check timeouts, retries or revise scores | [Budgets and measurements](#end-to-end-budget-and-missing-measurements) |
| Run a publishable cohort or build a report | [Certified cohorts and report builds](#certified-cohorts-and-report-builds) |
| Build a new report instance | [Report instances](#report-instances) |
| Score and publish WCXB | [WCXB scoring](#wcxb-scoring) · [timing and publication](#wcxb-paired-timing-and-publication) |


## Certified cohorts and report builds

For an explicit dataset population, export each suite with the declared annotation
revision first, then replace manual `--case-ids` with
`--population /path/to/definitions/populations/<revision>.json --population-suite articles`
(or the corresponding suite). The selection must bind `revision_sha256` and the
suite's frozen task hash to the exported dataset. Each suite's
`frozen_task_bindings` must also declare `exported_cases_sha256`, the canonical JSON
SHA-256 of the reviewed export's complete `cases` array (using
`harness.record_io.canonical_json_sha256`). Prepare this binding alongside the
reviewed population; the runner must not infer it from the input being checked.
Populations without this content binding are rejected before calls. Missing, duplicate, unexpected,
or mismatched cases fail before provider calls. Repeat for every declared suite;
a single-suite run does not certify the complete cross-suite population.
The output retains `task-population.json`; `execution.json` and the dataset's
comparison metadata record its hash, rule revision, input dataset hash, suite and
actual selected IDs. Existing explicit `--case-ids` runs retain their behavior.

Run every publishable Web Fetch provider cohort through `scripts/run_certified_benchmark.py`, which freezes executable task inputs. Existing compiled reference answers stay frozen; pre/post source checks require an explicit `source_verification.reference_mode: live_exact` task declaration. Missing newer acceptance-contract fields, source metadata and capture dates never imply a live-reference task. For a live-exact task, fail closed: verify and freeze references immediately before calls, verify the same declared scope afterward, and certify the cohort only when both checks pass. Direct `run_benchmark.py` output and incomplete cohorts are diagnostic evidence only; never assemble them into a certified release or ranking. An explicitly requested diagnostic report uses the diagnostic build and check entries and labels every page.

For reruns of unchanged compiled stable-contract datasets, invoke `scripts/run_certified_benchmark.py` normally. Freeze existing task definitions and acceptance rules; do not require historical qualification raw files or repeat admission review, and a missing old archive does not turn the run into a diagnostic result. Source qualification belongs to adding tasks or changing references/acceptance rules, through the matching build and revision flow; an explicit reference-answer revision still goes through the separate reference-revision entry. Preserve historical receipts without applying their evidence-file requirements to a new execution protocol. Live exact-reference cases retain pre/post reference checks. Reserve `--diagnostic` for explicitly diagnostic runs, whose outputs cannot be upgraded to certified evidence. `--retrieval-mode native` selects ordinary WebFetch completion while preserving scenario categories and every acceptance requirement; it rejects selector-guided retrieval hints instead of dropping them. Independent whole-page auditing, when requested, uses the existing browser capturer with `--observation-scope visible_page --checkpoint-dir ...`; checkpoint identities include capture inputs and observer code so observations from another protocol cannot be reused silently.

Keep one Web Fetch report implementation: `src/harness/web_fetch/report_build.py` constructs a report and `src/harness/web_fetch/report.py` renders it. `scripts/build_report.py` has no default build; select a report instance with `--profile` or name another of its modes. A profile build reads `--evidence`, falling back to persistent measured evidence (`~/.browser-eval/evidence/current/` by default, or `WEB_FETCH_EVIDENCE_DIR`), and writes to `--output` or the profile's `report_output`. A profile whose `comparison_kind` is `wcxb_frozen_html_to_markdown` selects the independently bound WCXB build, which requires `--evidence` and writes `reports/wcxb/` by default; `--wcxb` only checks that the profile is a WCXB one. Do not create versioned report implementations, compatibility renderers or redirect builds.

### Cohort-derived report content

Derive bilingual counts, budgets and method labels from the selected inputs.
Bind cohort-specific access and interface documentation in the participant registry;
reuse engine-level adapter contracts instead of adding per-version renderer tables.
Keep report-specific prose and its translations in explicitly bound content;
do not choose a historical article from provider names or task-family guesses.
Without a content binding, render the maintained data-derived explanation.
Report text the project writes has English as its source: `t(id, english)` in
the renderer, with Chinese in `messages.json`. `t` declares that English for
the post-render English pass, so write one text node per call.
A frozen or refresh build renders saved Chinese text without running those calls,
so keep the matching `report-translations.json` entries and regex rules.
Prove reuse through the public build and check entries with a different external
task/method inventory, not only by searching for old numbers in source code.
Exercise assembly and packaging with that external profile as well. Participant
IDs, labels, interfaces and comparison roles come from the instance registry;
a new cohort must not inherit a historical method's account or timing claims.
Disclose declared literal, fact, body-region, record, resource and identity checks
separately. A task instruction is not a full-text reference. Preserve frozen
verdicts while exposing passes without verified page identity, blocked-page
markers and unexpected resource URLs as diagnostics.

### Diagnostic reports

For explicitly requested live WebFetch diagnostics, use `make webfetch-diagnostic-report DIAGNOSTIC_PROFILE=/absolute/instance-profile.json OUTPUT=/absolute/new-output`; `EVIDENCE=/absolute/source` optionally supplies an explicit input. The `webfetch_cohort_diagnostic/1` model contains cohorts, per-case Boolean first-call verdicts, declared summary counts, cause/source labels, review counts and hash-bound reference/output/source/log files. `tests/test_report_templates.py:diagnostic_example` is a runnable two-scenario fixture through the public builder; its test command is registered alongside the variant. The release checker recomputes totals and validates rendered verdicts, complete evidence bindings, dialogs and links. A diagnostic remains diagnostic; this path cannot substitute for a certified build.

Diagnostic failure analysis derives unmet requirements and expected/actual values from the saved score-bearing call, separately from independent source observations and supplemental calls. Count each affected task once per failure type, disclose overlaps, and link counts to the matching task inventory. Source access failure and later success are observations, not engine root causes. Release checks recompute the type counts and detail explanations from bound evidence and reject contradictory or missing values.

Fresh diagnostic cohorts can supply a `webfetch_diagnostic_inputs/1` JSON file to the same build entry. It names complete saved run directories, independent whole-page browser observations and per-case reviews bound to dataset/results/observation hashes. The owner validates call and output identities and adapts these inputs to the existing model; no separate renderer is needed. Public copies may redact sensitive fields using the shared publication projector, with original/published hashes and an explicit local scope note; never alter the scored originals or present a redacted view as byte-identical. Review all requested passes as well as failures, retain access restrictions as observed limitations, and use actual requirements and source errors in interpretation specimens rather than storage metadata.

For a diagnostic release, freeze the complete intended population in the track profile's `cohort_plan` (group IDs, case counts and SHA-256 of sorted case IDs joined by newlines with a trailing newline), independently of the report's selected inputs. Bind a uniform implementation when required. Pass that profile with `--profile` to both `scripts/build_report.py --diagnostic-cohorts` and `uv run --frozen python -m harness.web_fetch diagnostic-check` before publishing. A self-consistent subtotal is not evidence that the requested matrix is complete; reject missing groups, substituted cases and mixed implementations. Template examples without a deployment profile remain local examples, not a substitute for this release gate.

A diagnostic instance profile can bind `diagnostic_input.path` relative to the data home and `diagnostic_input.sha256`. The ordinary build entry selects the diagnostic adapter from that profile, without a separate runner or mode flag. Keep cohort paths, prose, specimens and reviewed findings in the external manifest; refresh those inputs and compact profile bindings without editing the renderer or skill. Modify shared code only for a demonstrated defect or new measurement capability, and exercise multiple independent instances through the same public build/check entry.

### Fresh and composed cohorts

For a fresh browser cohort, extend the maintained Web Fetch interpretation mode with `comparison.report_interpretation: {path, sha256}`. Keep its JSON prose outside Git in the evidence bundle. The `webfetch_browser_interpretation/1` schema binds the normalized task/result input hash, a title, introduction, scoring explanation, conclusions, and one explanation plus specimen case ID for every participant. The owner computes aggregate and family results, all-call and successful-call latency, observed versions, complete program-error distributions, and evidence links. A participant with failures must show a failed specimen. Historical prose bindings remain scoped to their original evidence; do not replace their hashes to reuse old conclusions. `scripts/check_report.py` verifies current-cohort numerical cells, populations, unique highlights, chapters and live result targets on both the interpretation and standalone routes, with negative controls in `tests/test_headless_report.py`.

A composed cohort can use a compact current-report profile with `frozen_inputs` from `harness.web_fetch.storage.frozen_input_bindings` and an ordered per-track `case_inventory`. This binds the frozen dataset bytes and existing source/list-reference asset tree without duplicating whole datasets into a new repository folder. Both the build CLI and release checker validate these bindings and the complete provider-by-case matrix. The report package preserves original dataset bytes. These are publication-integrity checks on current inputs, not historical source-qualification requirements.

### Local-audit reports

For a new URL-only local matrix over committed stable contracts, reuse the local-audit renderer with comparison.kind=uniform_native_local_measurement and a pre-call local-run-contract.json. Require complete case-by-method coverage and one hash-bound public call log per result. This mode must state that results are machine judgments without independent review and must never inherit historical review receipts or certification.

The local-audit variant also supports a new uniform native local measurement. Bind the exact committed case file, scorer, providers, one run, 45-second outer timeout and zero retries before calls. Preserve product-default retrieval without per-case wait/extraction hints. Validate completeness and every output/log hash before rendering through the existing report owner. Complete retained debug logs may be exposed through sanitized local report assets; do not publish raw private diagnostics or claim inherited independent review.

### Published-snapshot audit

A published-snapshot score audit uses the maintained Web Fetch profile adapter,
not a copied renderer. Bind the complete original export, full historical matrix,
initial and corrected scores, per-input original/public/scored hashes, and every
changed decision. Preserve collection facts and timing. Evaluate a suspected
privacy effect with the historical scorer on the same public bytes; a redaction
receipt by itself does not establish causation. Keep source-backed annotation
corrections and executable regression provenance together. Reuse the native
report's routes, interpretation schema, structured history and packer, and run its
explicit snapshot audit browser gate before packaging. The catalog provides the
runnable example and commands. Store instance evidence and prose outside Git.

## Report revisions and file-delivery controls

Measured outputs are persistent local data, not source files. The current evidence
bundle lives at `~/.browser-eval/evidence/current/` by default; set
`WEB_FETCH_EVIDENCE_DIR` to use another explicit bundle. Raw research reruns live
under `~/.browser-eval/evidence/research/`; use
`WEB_FETCH_RESEARCH_EVIDENCE_DIR` when they are stored elsewhere. Hugging Face retains current public task catalogs and acceptance contracts; Git retains scorer code, synthetic fixtures, annotations and input hash manifests. Full
historical datasets and source captures are installed outside the checkout; see
[frozen reference inputs](../../../docs/reference-inputs.md). Git ignores provider artifacts,
results, execution manifests, selection receipts and internal audit notes.

`report-assets/report-changes.json`, bound beside the evidence, drives the public,
collapsible `changes/index.html`; `research/report-changes.json` is a frozen legacy
fallback. Record each substantive release's issue, change, score effect, evidence and
limits in the bound asset. Do not add a new report's results, prose, review scope or
publication URL to the legacy file.

Keep `research/provider-pricing.json` as a reusable product-price catalog: retain
verification dates, commitments, billing units and unknowns, but do not describe one
run or report there.

Keep the measured dataset/results untouched for presentation-only revisions.

Offline local revisions stage only their changed artifacts, receipts and current
dataset/results. The complete revision is validated before installation. A
per-snapshot lock and recoverable write journal restore every touched file after
an interrupted installation; the next revision recovers an unfinished journal
before scoring. Large unchanged captures are neither copied nor revalidated by
this transaction. Preparing and verifying native corrections use the same
projection, including bound same-call diagnostics and transport context.

The ranking column `判分完整性` is judged calls divided by planned calls, not a
success rate or text-completeness score. Every planned provider/case pair must
exist exactly once and be judged. Any missing, duplicate or unjudged pair marks
the report unreliable; validation and packaging reject it. Missing historical
file bytes cannot be reconstructed from a URL. An independent HTTP control can
exercise the byte pipeline without pretending to be a provider result:

```bash
uv run --frozen browser-harness-eval run \
  --dataset evaluation/dataset.json \
  --provider-config configs/providers.example.json --providers http-local \
  --task-kind file --output ~/.browser-eval/evidence/research/file-delivery-check
```

This selects the dataset's file tasks and records HTTP calls, returned binaries,
format/length checks and target hashes where declared. The report labels the run
as diagnostic. HTTP delivery is separate from browser execution; it cannot fill
historical browser observations. To check a browser's file delivery, use its
configured provider name with the same command. Text or links alone are not a
file deliverable. The output directory must be new.

`--case-ids` selects explicit tasks; combining it with `--task-kind` takes their
intersection. An empty selection or repeated task/provider names fails before
calls. The diagnostic matrix contains exactly the selected tasks and methods;
it does not inherit a historical cohort's certification or review requirements.

The shared runner rejects empty or duplicate execution scopes before adapter
validation or calls. Run counts and concurrency must be positive integers;
timeouts must be positive and finite, and delays finite and nonnegative. Task
IDs, HTTP(S) URLs, required fields, output types and report groups are validated
on dataset loading and again at execution. `models.py` owns the output-type and
report-group registry used by configuration, execution and presentation.

## Independent browser observations

`browser_observations.py` owns browser reference capture; the corresponding
`uv run --frozen python -m harness.web_fetch headless-observations` command runs it. Install the `headless-eval` extra and a Playwright Chromium
browser, or pass `--browser-binary` with an installed Chromium executable. Batch capture requires POSIX process isolation.
New tasks declare their completion and content scope in the task definition:

```json
{
  "source_verification": {
    "browser_observation": {
      "selector": ".article-card",
      "minimum": 10,
      "content_selector": "main",
      "text_mode": "visible"
    }
  }
}
```

The completion selector may match multiple elements; the content selector must
match exactly one container. `visible` captures visible text, `dom` deliberately
includes preloaded hidden text, and `reference` applies the shared reference-text
pruning rules without computing CSS visibility. Without a policy, reference capture uses the declared source
selector or `body`. Whole-page audits use `--observation-scope visible_page` and
include visible embedded frames. Historical special policies are compact data
contracts bound to exact frozen task definitions; changing those tasks requires
an explicit policy rather than silently inheriting behavior from their names.

Navigation and extraction share the per-attempt time budget. Recorded elapsed
time is never truncated; an observation exceeding the budget cannot satisfy
completion. `--checkpoint-dir` requires `--browser-binary`: checkpoints bind the
binary, task, scope, budgets and capture implementation. Changed inputs or
modified checkpoint contents are rejected. Use a new checkpoint directory for
a changed experiment. Checkpoints and final outputs are created exclusively and
cannot replace an existing observation. Store them under the configured data
home, outside Git.

The batch isolates each browser worker in a child process. Its wall-clock budget
covers all declared attempts plus two seconds for startup and cleanup, including
browser transport calls that do not honor Playwright's page timeout. A stuck or
crashed worker produces a failed observation and leaves other cases intact.

Explicit `source_verification.reference_mode: live_exact` applies to every track
and output kind, including stable contracts and mixed text/resource deliveries.
A required live reference must exist before execution. Without that declaration,
compiled references remain frozen. Rendered-reference checks apply the declared
body regions, record contracts, composite fields and patterns as well as facts;
a fact group is optional when another
content requirement establishes the source scope.

## WCXB scoring

Current qualified inputs come from [LexBench-WCXB on Hugging Face](https://huggingface.co/datasets/Lexmount/LexBench-WCXB), selected by the [data lock](../benchmarks/data-lock.json). Browser Eval owns the runner and scorer. Existing experiment profiles keep their original source manifests and input hashes.

`wcxb_scoring.py` owns certified frozen-HTML scoring. The maintained command is
`uv run --frozen python -m harness.web_fetch wcxb-score`; the required `--profile` argument selects an
external experiment profile. The profile's ordered
participants must match the cohort certificate exactly. Adding or renaming a
participant in a separately certified cohort requires data changes rather than
scorer branches. This scoring option does not change the current report's
publication authority or the certification runner's supported participants.

`wcxb_metrics.py` owns the metric functions and everything they call, and may
import only the standard library. `data/wcxb-metric-bindings.json` binds a digest
of the module's AST without docstrings, so formatting and comments keep the
binding while any code change breaks it until the authority is revised on purpose.
The `word_f1` and `snippet_check` names in `wcxb_scoring.py` must also be this
module's functions, so rebinding either name is rejected. This catches drift. It
does not prove what `_score` calls, and `wcxb_scoring.py` itself is not bound.
The authority also maps the digest named by existing evidence to the module digest
it represents, so frozen profiles, certificates, summaries and timing receipts keep
verifying without being rewritten.

For WCXB execution, the external profile also declares `expected_versions`, a
map from each participant ID to its exact reported CLI version. Use `null` only
for an interface that exposes no version. This machine identity is separate from
the display label and the profile-bound binary hash. No historical report supplies
a default browser version.

The scorer loads the profile-pinned evaluator bytes and validates the complete
source-qualified scope, execution identity, logical outputs, every retained
physical response and final-call selection. When a profile binds a full paired
timing correction, binary hashes join its timing roles to certified participants;
browser names never select corrected values. All-call and successful-call
latency populations remain separate, and retries retain their physical evidence.

Scoring stages derived results before installation. Invalid or changing inputs
produce no score files. Handled installation failures remove newly created
outputs while preserving existing evidence; existing score files cannot be
overwritten. The summary is installed last. Rebuilding a historical report uses
its frozen scores, not an implicit rescore or new provider calls.

## Common text evaluation

Website, dynamic-site, popular-site and document text share `scoring.score_attempt`:
request eligibility, error-page checks, declared text/record requirements, resource
checks where applicable, and one authoritative task predicate. Live execution and
offline rescoring use the same entry point. Case contracts define the differences;
provider identity never selects a more lenient evaluator.

Adapters declare `answer_format` and retain structurally selected answer fields.
The common scorer accepts `page_structure.headings` as standalone title candidates,
and `records` as explicit field dictionaries subject to the existing completeness,
uniqueness and source-membership checks. Flat records cannot establish a scoped
section or bypass excluded page regions. Returned link labels never satisfy
facts or page-text anchors. These fields are not inferred from document metadata,
request echoes or diagnostic logs. Adapters keep only text values and text lists
from returned fields; a field of another shape is absent, not an error.
Native projections bind every selected field to the original response. A changed
projection over saved responses is an explicit offline correction, never a rewrite
of the historical pre-call declaration or an additional provider call.

The optional `page_information` object preserves explicitly extracted titles,
authors, dates and other declared page text as separate values. Unscoped fact and
page-identity checks may read them without concatenating fields. They do not
increase body length or coverage, supply records, or bypass region exclusions.
An extracted title may satisfy an exact title contract; generic HTML document
metadata (including a document description or site name), search keywords, image
alternatives and debug logs remain separate.

Plain-text post lists require a blank line before each later author line and
permalink. A name and post URL inside body prose do not establish another record.
Markdown post headings and native HTML post containers provide explicit record
boundaries.

Text acceptance reads the visible prose of raw HTML embedded in Markdown. HTML
attributes, scripts and explicitly hidden elements cannot supply body coverage
or required facts. Literal HTML in code examples remains answer text. This
projection runs inside the shared scorer and leaves provider artifacts unchanged.
Unescaped angle-bracket text that matches an HTML tag is treated as markup,
including `List<String>` and `x<y then z>w`. Technical notation must use code
spans, fenced code or escaped brackets to be unambiguously literal. The parser
does not guess from tag names whether prose was intended; custom hidden elements
must remain hidden. A comparison opener without a closing bracket before the next
tag is retained as text.
`required_patterns` defaults to `match_scope: "text"`; a rule that checks serialized
output structure, such as an HTML table tag, must declare `match_scope: "output"`.
Unknown scopes are rejected. Use paired controls in `src/harness/benchmarks/scoring-regressions.json`
to guard equivalent prose, attribute-only answers and structural output.

Reruns of an unchanged compiled stable-contract dataset use
`scripts/run_certified_benchmark.py` normally: freeze existing task and acceptance
rule bytes without recovering historical qualification files or rechecking
admission. Source qualification is required when adding tasks or changing
references or acceptance rules. Live exact-reference cases retain their pre/post
reference checks. Use `--diagnostic` only when a diagnostic run is intended; its
outputs cannot be upgraded to certified evidence.

This applies to resource targets as well as text answers. Execution and report
checks use the frozen resource target list without reopening historical source
qualification files. Adding or changing a resource task still requires independent
source qualification; an invalid source state or an explicitly live reference
cannot take the frozen-input route.

For declared native completion, add `--retrieval-mode native`: it preserves
acceptance rules and rejects existing
selector-guided retrieval hints rather than dropping them.
Dynamic task families stay `dynamic`: the explicit
`retrieval_hints: {"version": 3, "mode": "native"}` contract selects each
provider's ordinary completion without injecting selectors, extraction hints or
extra waits. A small number of independently qualified page shapes may add only
`"contract": "<registered-name>"`. The shared registry in
`evaluation/native-retrieval-contracts.json` binds each name to one case and one
hash-verified source observation. Dataset rows cannot carry selectors, counts,
delays or answer text directly; unknown names and names used by another case fail
closed. Selecting native mode again preserves either form unchanged, and the
runner validates it through the same strict parser.
Version 1/2 selector-guided contracts keep their existing meaning.
The diagnostic report accepts a `webfetch_diagnostic_inputs/1` manifest naming
complete runs, independently captured whole-page observations and hash-bound
per-case reviews. The supported builder adapts these inputs, preserves scored
originals, binds any public redaction and runs the maintained release checks.

### Report instances

Live WebFetch reports are data instances, not renderer subclasses. A diagnostic instance's
external profile binds its complete `cohort_plan`,
implementation, external `diagnostic_input` (data-home-relative path and SHA-256),
and publication target. The input manifest owns cohort evidence paths, reviewed
findings, explanatory prose and examples. It does not contain rendering code.

```bash
uv run --frozen python scripts/build_report.py --profile /absolute/instance/profile.json --output <new-report-directory>
uv run --frozen python -m harness.web_fetch diagnostic-check <new-report-directory> --profile /absolute/instance/profile.json
```

Set `BROWSER_EVAL_DATA_HOME` when the external evidence uses a nondefault root.
Changing a run, version, group, sample, explanation or example updates instance
data and bindings; it does not require editing Python, HTML, skills or a private
assembly script. Shared execution, scoring, evidence adaptation, rendering and
release validation remain separate owners. A demonstrated shared defect or a
genuinely new measurement contract may require code and behavioral tests; an
ordinary report refresh does not. Certified and diagnostic evidence keep their
different validation contracts rather than gaining interchangeable labels.
Provider versions and runtime settings can also be instance-local: pass
`--provider-config <instance-provider.json>` to the certified/diagnostic execution
entry. The shared runner retains its exact configuration hash in each execution
batch; changing one instance does not overwrite another report's defaults.
Use Artifact Site's official CLI for transport and recovery, supplying the
profile-bound host, slug and expected version. An update does not create a share.
After export, `diagnostic-check --deployed-archive <export.zip>` compares
the complete deployed tree with the locally checked instance, byte for byte.

The answer envelope accepts body text and explicitly paired name/link records
from the returned `links` field (including internal/external groups). Arbitrary
logs, request echoes and bare URLs are not named entities. HTML and Markdown
including single soft breaks use the same link validator. Resource discovery
reads only declared answer-resource fields, not arbitrary nested diagnostics.
Explicit successful empty API envelopes are normalized by their response fields,
not by provider name. Original adapter status remains in `raw_request_status`;
the report never changes the meaning of a request.

Full-body cases may declare `body_requirements`: up to five contiguous regions
of the independent reference. Version 3 compares six-character windows after
removing layout syntax and link destinations. Each returned window can satisfy
only one reference region. Every region has a 60% hard floor, and all but at most
one must reach 80%. This rejects small prefixes and repeated short-fragment
borrowing without using aggregate F1 as an acceptance gate, while
allowing one mildly noisy conversion region. It does not verify semantics. The
thresholds and regions are stored in each case, never selected by provider.

A `body_text` acceptance contract can declare `stable_text_role: diagnostic`
when the case also supplies `body_requirements` or `composite_acceptance`.
The declared body and record requirements remain mandatory; stable anchor hits,
misses and coverage are retained as diagnostics. A diagnostic role without either
content contract is rejected. Other schemas and contracts that omit the role keep
their existing mandatory anchors. The risk-control compiler assigns this role
from the independent contracts, without reading provider outputs.

## End-to-end budget and missing measurements

The common call boundary includes provider acquisition, network retries, rendering,
local extraction and required answer normalization. Artifact persistence, scoring,
reporting and between-call throttling are outside this latency. Runtime source and
configuration hashes are captured before calls. Moli binary hashing occurs during
validation, not each measured fetch; a changed binary requires revalidation.

### Retries

A repair run may retain successful results and rerun only cases with a declared cause (changed input, provider route or version, runtime contract, source validity or infrastructure failure), not failures as such; without one, label the results cumulative and diagnostic. A predeclared failure retry is part of the run contract. Keep every physical call and select the final response under the declared retry/correction policy. Recompute scores for the complete selected result population when the scorer changes; rescoring does not require new provider calls. If executable builds differ, disclose their fingerprints and selected result counts as cumulative results. This does not establish the full-suite result or speed of one build.


All methods default to one retry after a failed task. Set
`--max-failure-retries N` to any nonnegative integer; `0` disables retries.
There are at most N+1 calls per task, stopping as soon as the task passes.
The same rule covers request errors, timeouts, empty output and content-rule
failures, including methods using native parameters.

Each method and task produces one current authoritative result. The final call,
never the best one, replaces the earlier answer, verdict, metrics and profile-defined
primary latency; retries do not add samples or a separate result category. Call artifacts remain available for audit, with
accumulated execution cost stored separately from the final-call latency.
An uncancellable call must stop before another starts; a still-running call
makes the cohort incomplete. Historical cohorts retain their frozen retry policy.

### Timeouts

Late answers cannot pass. A common daemon boundary stops accepting output at the
deadline and requests cancellation. An adapter that cannot cancel is quarantined:
remaining calls on it are recorded as unexecuted, not fabricated failures, and
the run exits with an incomplete-evaluation error. This avoids overlapping
abandoned requests while preventing a partial run from being treated as a
finished benchmark. Native subprocess adapters cancel their processes; cleanup
is never extra answer budget.

### File bytes and missing measurements

New file evaluations accept `ProviderResult.files` entries containing actual
`data: bytes`, retain separate binary artifacts and verify signature, length,
SHA-256 and a declared independent target hash when present. Link-only output
cannot pass a measured byte contract. This does not decode or understand media.
Historical records without `raw_file_pass` are unmeasured even if their HTTP
request failed: all report/summary denominators and failure filters exclude them.
The three result filters remain pass/fail/all; unmeasured rows appear in all.

### Correct a score or reference

`uv run --frozen python -m harness.web_fetch content-rescore` recomputes
all applicable records through the shared scorer without supplier calls.
Known scoring failure modes have compact synthetic positive and negative controls
in `src/harness/benchmarks/scoring-regressions.json`; CI executes every control through the
same `score_attempt` orchestration and task predicate used by live and offline
scoring. A new decision-changing scoring correction, or a frozen-reference
correction via `uv run --frozen python -m harness.web_fetch reference-revision`, must use its revision
command with a concrete `--root-cause-category`,
`--root-cause`, and one or more `--regression-id` values. The command validates
those controls before touching evidence and writes their IDs plus FP/FN direction
counts into the revision receipt. Directly editing stored verdicts is not a
supported correction path.

Reference corrections use `harness.web_fetch.reference_revision` through the
`reference-revision` command. It validates
existing revision histories before any change, stages the full evidence bundle,
and copies only the selected cases' corrected reference inputs. A failed copy,
score or receipt write leaves the original bundle intact. Success retains the
predecessor beside the evidence directory under `.NAME-reference-history-*/original`;
these private copies preserve prior references and are excluded from the report.

Offline rescoring and scoring revisions are available through
`uv run --frozen python -m harness.web_fetch content-rescore --help` and
`uv run --frozen python -m harness.web_fetch scoring-revision --help`.
Supply an evidence directory and, for rule revisions, an explicit canonical
input plus the required root cause and executable regression IDs. Neither
operation calls providers. Declared evidence must remain present, unmodified
and free of symbolic links before scores can be rewritten.

An authorized error correction should complete automatically: first find the
shared cause and add the necessary positive and negative controls, then freeze the
complete affected call scope and rerun it through the certified entry. Interface,
version or execution errors need new calls; scoring or presentation errors only
recompute or rebuild. Do not pick failed tasks to retry for a higher score, do not
repeat calls for unrelated tasks, and do not turn a rerun of an existing task set
into a new collection of qualification evidence. Retain the old results and the
effects of the revision.

A benchmark configuration error or an adapter block before a call is an execution
problem and must not be published as a browser capability failure; the native route
gate rejects `configuration` results. Errors returned by an actual provider call are
still retained under the existing rules.

### Retain or merge an existing review

Existing independent judgments can be retained after a score-only correction
with `uv run --frozen python -m harness.web_fetch review-rebase`. It requires identical task and
reviewer-visible inputs, retains the independent verdicts, and requires explicit
adjudication for remaining disagreements. `--output` stages a validated blind
review; `--install` also requires the complete publication review checks.
The output cannot overwrite input evidence or the prior review.

`uv run --frozen python -m harness.web_fetch review-merge` combines a prior complete review with
already recorded supplemental judgments. Use `--prior-review` for a bound scoring
revision chain, or `--prior-evidence` for a refreshed result matrix. Changed tasks
invalidate reviews across all methods; rerun outputs invalidate the affected
attempts. Both commands validate before installation and leave the installed
review unchanged on validation or write failure. They do not initiate a review;
independent review still requires an explicit request.
A merged blind review still needs any anomaly investigation required by the
dataset before publication.

### Request an independent review

Independent execution remains opt-in through `uv run --frozen python -m harness.web_fetch review-headless`;
its default scope is machine failures. Reviewing passes or the full matrix also
requires `--confirm-full-review`. `--prepare-only` creates blinded packets without
starting a reviewer. `uv run --frozen python -m harness.web_fetch review-anomalies` investigates an already
recorded blind review without changing its independent judgments.

Keep each command's `--work` directory outside Git and measured evidence. Its private
`review-session.json` binds the frozen inputs, verified artifact hashes, exact
reviewer configuration and review implementation. Matching inputs can resume;
changed inputs, models or implementations require a new work directory. Existing
unbound work directories are preserved and rejected rather than silently reused.
Changing task IDs is not required to invalidate stale judgments. Provider-filtered
reviews remain partial and cannot replace a required complete installed review.

### Compare timing populations

For a heterogeneous same-window
cohort, keep every predeclared call and report all-call latency (one shared task
denominator) separately from successful-call latency (each method's conditional
subset). The all-method common-pass intersection is diagnostic only: never
reselect tasks after seeing outcomes.

### Text coverage and incomplete references

`content_check_coverage` measures matched required text groups, not body recall.
All declared groups are required by stable contracts; record count/type/uniqueness
remain independent requirements. Empty text groups yield null coverage. The report
shows the number of covered cases and does not equate unequal check difficulty.

Only complete comparable references support body precision, recall and F1. Partial
headings/labels cannot support precision over a whole output: additional legitimate
text is unjudged, not noise. Snapshot scores containing explicitly nonliteral fields
are retained as attempt diagnostics but excluded from invariant body aggregates.
Returned-title quality is also auxiliary and never substitutes for body scores.

### Popular-site references

Popular-site stable text must be independently observed and task-relevant. Do not
freeze volatile ranks, prices, member counts or current list membership. Title
diagnostics join returned entities to independent names by detail URL and never
substitute for body quality, recall, freshness or task acceptance.

Popular-site stable text is backed by `sources/<case>/stable-page.txt` and capture
metadata, or the original independently saved `reference.txt`. Binding verifies each
literal against its evidence and stores its SHA-256 before offline rescoring. Nine
current popular cases have such groups. Zhihu's retained list reference has no
stable non-item text; its text coverage remains unmeasured, while its existing list
result remains visible. Amazon only requires the independently observed shared query
context because Chinese and English filter-label equivalence has not been captured.
Do not invent missing context or silently count these cases as fully annotated.

Rule expansion rescoring is labelled offline and preserves original calls, outputs,
timings and run IDs; it is not a new provider run. Changing list members, order,
prices or popularity values must not change stable acceptance. Removing required
stable text, returning an error page or omitting required records must fail.

## Popular-site title quality

`title_quality.py` computes a supplemental diagnostic during report builds from
persisted `stable_records` and independent `list-references/<case>.json` evidence.
It joins each returned entity by detail URL, accepts independently captured name
aliases, and compares title tokens (CJK characters / other-language words).
Recall, precision and F1 are averaged within each case, then across covered cases.
Neither list membership/order nor prices are fixed answers. Unknown identities and
no returned titles are unmeasured, not zero. The table reports both title and case
coverage; providers may have different measured subsets. A high conditional title
score cannot establish retrieval success, freshness, factual correctness or full
body quality. Edited titles can differ legitimately from the captured names.

The generated `results.jsonl` includes per-title comparisons, source timestamps,
reference/output/scorer hashes and `offline_title_diagnostic` provenance. Original
evaluation records, task decisions and `word_*` metrics are unchanged. Full-body
metrics require a separately scoped, comparable complete reference.

Web Fetch providers, execution, reference checks, scoring and report generation
live in this package. The fixed-action kernel benchmark remains independent in
`src/harness/trajectory_replay`; autonomous browser-use evaluation lives in
`src/harness/browser_use`.

From the repository root:

The fixture commands need no historical inputs. The Moli command below uses the
frozen historical dataset; first [import its approved input copy](../../../docs/reference-inputs.md).
Its `evaluation/dataset.json` name resolves through the input manifest, while the
public task directory contains only metadata and definition hashes.

```sh
make setup
uv run --frozen python -m harness.web_fetch validate --providers fixture
uv run --frozen python -m harness.web_fetch run --providers fixture
uv run --frozen python -m harness.web_fetch run --dataset evaluation/dataset.json --provider-config configs/providers.example.json --providers moli-webfetch --case-ids static-simple
make report PROFILE=/absolute/path/to/profile.json EVIDENCE=/absolute/path/to/evidence
```

Credentials and local executable/repository paths belong in the root `.env`
(copy `.env.example` and fill only the methods you use). Evaluation credentials
come only from the repository `.env`: exported values are ignored and an external
`--env-file` that sets one is rejected. For non-credential runtime paths, exported
environment variables take precedence and `--env-file` selects which file is read.

`moli-webfetch` invokes the official local Moli CLI; set `MOLI_BINARY` when it
is not on PATH. All webpage and document-body tasks use the CLI's native
Markdown output. Webpage media-information tasks use that same native Markdown
call with native frame inclusion; the shared scorer checks only the requested
resource URLs already present in the output and does not download the media.
Direct-media-file tasks use the CLI's raw response path so the evaluator can
verify the original bytes. These routes are selected by deliverable before
execution and do not fall back after failure. The six measured configurations
and their document limits are defined in [HEADLESS.md](HEADLESS.md).

The installed console command is `browser-harness-eval`. There is no legacy
Python package or compatibility entrypoint.

## Independent source capture

Install the `reference-audit` extra for HTTP and document capture. Select the
dataset scope explicitly and write outside the source checkout:

```sh
uv run --frozen --extra reference-audit python scripts/collect_source_evidence.py \
  /absolute/path/dataset.json /absolute/path/new-captures \
  --only case-a,case-b --workers 2
```

The destination must be new. The command validates case IDs before requests,
retains source bytes and hashes, and records HTTP failures as `needs_review`.
Unknown or repeated IDs are rejected. If a worker is interrupted, no partial
capture package is committed. An optional repeated `--resolve HOST=IP` records
an explicit DNS override and restores the resolver after the batch.

`source_capture.py` owns capture and HTML extraction; all reference consumers
use this module. Document extraction reuses `document_text.py`. Capture does
not update the dataset, approve ground truth or certify provider calls. The
certified runner owns pre-call and post-call capture and reference validation.

## Dynamic-content ground-truth protocol

Reference capture retains paired initial HTTP bytes and independently rendered
visible text, and bounds completion and the full declared scope. Distinguish embedded
state from follow-up fetching; never require JavaScript as the only valid route.

Merge and append measured dynamic supplements through the same shared module.
Pass private input directories explicitly; merged output must be new.
These commands make no provider calls and do not rescore:

```sh
uv run --frozen python -m harness.web_fetch dynamic-merge \
  --reference /absolute/path/frozen-reference \
  --runs /absolute/path/run-a /absolute/path/run-b \
  --output /absolute/path/new-assembled-supplement
uv run --frozen python -m harness.web_fetch dynamic-append \
  --source /absolute/path/current-evidence \
  --reference /absolute/path/frozen-reference \
  --measured /absolute/path/new-assembled-supplement \
  --checks /absolute/path/independent-completion-checks.json
```

Merge preserves original record bytes, auxiliary outputs, retry artifacts, private
responses and certified post-call reference checks. Append validates task rules,
complete method coverage and independent completion conditions; changed task rules
cannot reuse earlier measurements. Copy failures expose no new merged directory;
append failures preserve original evidence. Both commands support `--help`.
They issue no new certification; formal publication still needs the track's
profile and release gates.

Keep dynamic-content results separate from the existing body/access/resource
tracks. A category named `javascript_rendering`, a browser success, an HTTP
failure, or an empty static extractor result does not establish that JavaScript
execution is necessary. Inline JSON can supply the entire answer without JS.

For each case, independently capture and retain:

1. The initial HTTP response body (before script execution), URL/final URL,
   status, capture time and encoding. Preserve original response bytes; supply
   UTF-8 HTML to the validator (if transcoded, retain both original and UTF-8
   hashes). Do not substitute `page.content()` for the initial response.
2. Actual browser-visible target text after an explicit, bounded completion
   condition. Capture visible DOM evidence separately from HTTP HTML, with
   selector/scope, observed item count, elapsed time and the condition outcome.
   Use scoped visible text/DOM evidence, not script-containing `textContent`.
   Save the rendered DOM or screenshot alongside text when useful for review.
3. The complete reference for that declared scope, derived independently of
   provider outputs. Freeze the applicable body, record, field, pattern or fact
   requirements before scoring and check them against both reference and rendered
   text. Each declared fact group needs a nonempty alternative. Include tail and
   item-count checks to catch partial lists.

Use a condition such as “12 product cards visible and loading indicator gone,
within 10 seconds,” not merely `networkidle` or a fixed sleep. A delayed request
can start after network idle; background requests can prevent it indefinitely.
On timeout, truncation or an incomplete list, preserve failure evidence and do
not shorten the reference, lower `min_content_chars`, or remove missing facts.
For infinite scroll, declare a finite initial batch or explicit scroll count;
click/scroll workflows belong in Browser Use, not an implicit URL-only fetch.

The pure, standard-library validator performs no I/O, browser work, environment
loading or writes:

```python
from harness.web_fetch.render_evidence import validate_render_reference

# case may be a dataset dict or EvalCase. These are capture observations,
# not provider controls and not a substitute for retained browser evidence.
case["source_benchmark"]["render_evidence"] = {
    "scope": "All 12 cards in the initially loaded product list; no scrolling",
    "rendered_truncated": False,
    "completion": {
        "condition": "12 product cards visible and loading indicator gone",
        "timeout_ms": 10000,
        "satisfied": True,
    },
}
audit = validate_render_reference(case, initial_html, rendered_text)
assert audit["valid"], audit["errors"]
```

The result includes exact SHA-256 hashes of initial HTML, rendered text and
`ground_truth_main_content`, plus group-by-group coverage for initial static
DOM text, initial payload candidates, rendered text and reference. Matching
uses the scoring normalizer and numeric boundaries; all groups are mandatory
regardless of `fact_coverage_threshold`. Without fact groups, another declared
content requirement must supply acceptance. Numeric fields require values;
labels or loading placeholders do not establish a completed dashboard.

Classifications describe the required facts, not the entire page:

- `initial_visible_content`: all groups found in a static DOM text proxy.
- `embedded_payload`: all groups recoverable across initial DOM/source literals,
  but not all in static DOM text. This rules out a “must execute JS” claim based
  on these facts alone. It includes hidden/template/metadata content.
- `partial_initial_content`: only some groups found in the initial response.
- `absent_from_initial_evidence`: no groups found by this mechanical audit.
- `unclassified`: no fact groups were supplied. Other declared content checks
  still determine validity.

The static visibility proxy excludes scripts, head, templates, noscript and
explicitly hidden elements; it cannot compute stylesheets, layout or browser
HTML repair. Payload detection recognizes source literals and JSON string
escapes, not arbitrary expressions, compression or external scripts. Therefore
absence is not proof of JS necessity: a follow-up HTTP/API request may suffice.
`js_required_proven` is always false. Likewise, `valid` checks declared
completion, length and declared content gates; it does not independently verify the declared
capture outcome or prove full-page completeness. Retain the actual evidence.

Run the synthetic checks without credentials or network:

```sh
uv run --frozen python -B -m unittest discover -s tests -p 'test_render_evidence.py'
```

Execution pitfalls: the fetch runner calls `provider.fetch(case, timeout)`;
subprocess workers currently receive only URL and timeout, not selectors or
completion conditions. Metadata above does not change their wait behavior.
Use a separate dataset and output directory for each measurement cohort.
`track: dynamic` identifies dynamic website body content. Popular-site list identity is a
separate structure task, evaluated through the shared scoring contract. Run a
new certified cohort with the declared tasks and independent list references;
never rewrite an existing dataset or replace its measured rows in place.
Local fixture URLs are unavailable to cloud providers and are blocked by some
private-network guards; compare them locally or deliberately host a public
fixture. Avoid the answer-replaying `fixture` provider as a rendering baseline.

## Stable acceptance and text quality

Rendering requirements and value stability are separate axes. Most of the
dynamic examples are fixed demonstration data delivered after loading; popular
rankings and catalogs change frequently. Annotate fields, not whole websites:

- Stable document titles, headings and instructions can be explicit
  `stable_text_anchors`. Every alternative must be grounded in the same frozen
  page and declared content scope. Formatting variants and explicitly
  interchangeable statements within that page are allowed; alternatives must
  not accept different regional, historical, or redesigned page bodies. DNS,
  redirects, access failures, and variant page responses are retrieval
  outcomes, not reasons to add acceptable answers.
  Fix a wrong source or reference only from independent evidence. A change to
  the target URL requires new calls for every affected method; retain the old
  task and calls as historical evidence.
- Frequently changing names, prices, dates and ranking positions use an
  `acceptance_contract`: required fields, types, record grouping, minimum count,
  uniqueness and stable page text. Values are not compared to a previous
  ranking. Duplicate cards cannot satisfy the minimum. Final-URL agreement is
  diagnostic: a missing or mismatched URL does not change acceptance or the
  evaluated denominator. HTML document-language tags are also diagnostic;
  content acceptance comes from the declared answer requirements.
- Full-reference word precision, recall and F1 remain separate diagnostics.
  Historical demonstration snapshots describe their captured sample; they do
  not certify arbitrary later content. Without a comparable full reference,
  leave these metrics unavailable while still evaluating the task contract.

`stable_tasks.py` validates the contract before evaluation. Stable tasks do not
need a fresh live answer or post-call value comparison. Output hashes and rule
hashes remain integrity/provenance evidence, not tests of truth or freshness.
Maintain synthetic positive and negative tests: changed volatile values and
equivalent formatting must not change acceptance; missing stable anchors,
missing fields, duplicates, wrong types and login-only outputs must fail.
An explicit rule change can rescore immutable outputs, but must be recorded as
rescoring rather than a new provider run. A site redesign may still require
task maintenance; stable rules are not a claim of permanent validity.

## Exact live-snapshot comparisons

Rendering requirements and data freshness are independent. A JavaScript catalog
may contain stable example data; a server-rendered news page may change every
minute. No captured answer remains valid indefinitely.

For new exact live-body comparisons with source-verification metadata and no
validated stable acceptance contract, the following safeguards still apply:

1. Independently capture the complete declared scope within 30 minutes before
   each provider call. Freeze the text, verification timestamp, SHA-256, task
   scope and fact rules. Derive names and values from this capture, not from
   provider results or last run's answer. The runner checks freshness before
   each exact-snapshot call; stable-contract preparation is a separate mode.
2. Preserve initial HTTP bytes and visible rendering/completion evidence for
   dynamic cases. Fixed initial-batch sizes are task boundaries, not permission
   to reuse old titles. If a whole-catalog count changes, stop and revise the
   declared scope with independent evidence; never silently shorten the answer
   to make a provider pass.
3. After the last relevant call, independently recapture the same complete
   scope within 30 minutes. Submit the observation to
   `uv run --frozen python -m harness.web_fetch reference-finalize --run-dir RUN --observations FILE`.
   The JSON observation contains `case_id`, `independent: true`, `checked_at`,
   `content_text`, the exact `scope_identity` from the manifest binding, and
   `completion: {satisfied, elapsed_ms, timeout_ms}`. These fields attest to
   retained independent observations; copying candidate output is prohibited.
4. Publish new guarded runs only after the postcheck is verified. Missing,
   expired or changed references suspend the measurement; they are not provider
   failures. Keep raw outputs and the failed check, and schedule a fresh cohort.

Use short batches when the full provider suite exceeds the time window. The
30-minute window is a conservative operational limit, not proof that a site
cannot change within it. Matching pre/post captures bounds observed drift but
cannot rule out an unobserved intermediate change. Historical runs lacking this
protocol remain dated observations and cannot be certified retroactively.


### Native caller limits and DNS

With `uniform_native_parameters`, webpage body/structure calls use the same
native request regardless of the web, dynamic, popular (`challenge`), or
risk-control family. Case-specific retrieval hints remain prohibited. Scoring
keeps the original case and frozen acceptance contract; family is not a reason
to inject scripts, convert HTML to Markdown, or fetch another URL.

New fixed-native cohorts declare `source_benchmark.input_media_kind` for every
case (`html_page`, `direct_pdf`, `direct_docx`, `direct_text`, `direct_image`, or
`direct_media`). JSONL workers receive the same declaration as
`input_media_kind`. Native document routes use this frozen type, not URL suffixes
or expected answers. Historical calls without this declaration are not silently
upgraded to the new protocol.

Before any case call, the runner writes `native-route-contract.json`, binding
every method/track/task-kind/input-type cell to its interface, answer fields,
configuration and normalized transport parameters. Actual arguments are checked
before transport; changing the task URL, endpoint or parameters fails the run.
Each physical call permits one adapter invocation and retains its own request
receipt, including every caller retry. Each receipt binds to the pre-call contract.
Aggregation retains the exact contract bytes; report and release checks verify
its hash, complete declared scope and per-result receipts. A retrospective audit
of old logs cannot substitute for this pre-call proof.

New contracts also freeze the deterministic answer-selection policy in
`native_projection.py`, retaining its hash-bound source once per run. Adapters
and saved-evidence validation share that policy. Fixed response-structure and
transport-status branches are allowed; choosing another source after the call
is rejected even when both fields contain identical bytes. Validation never
executes retained source code: reproduce with the matching implementation when
the policy hash differs. Historical declarations without this policy binding
continue to prove field reconstruction only, not a pre-call selection rule.

Resource tasks may select a declared native output: Firecrawl image/link/video
fields, Cloudflare's generic media-element scrape, and Crawl4AI media fields.
Moli uses native HTML for resource discovery and native Markdown for webpage
body/structure, without caller-authored extraction scripts.
File tasks may collect Moli's raw output, Firecrawl rawBase64, or Crawl4AI's
native downloaded files. No caller follows returned links to manufacture files.
Document text uses the product's own URL handling; Crawl4AI direct PDFs select
its native PDF strategies. Moli direct PDF/DOCX responses remain original
file bytes, not extracted text; a body contract cannot pass on bytes alone.
The fixed mode never uses the legacy Moli external
document parser or Cloudflare download-then-upload converter. Unsupported or
incomplete native output remains incomplete under the unchanged scoring rules.
Cloudflare's fixed media selectors are independent of reference answers, and
native resource/PDF routes retain product-default internal timeouts.


Native workers receive the caller deadline and fixed native capacity options,
and record effective limits. In fixed-native mode, the outer deadline does not
override product timers. CowAgent uses its native `DEFAULT_TIMEOUT` unchanged.
Workers do not add case-specific retrieval rules. Caller failure retries follow
the shared [retry policy](#retries).

The runner and validation entry point check system DNS before guarded native
methods execute. Synthetic `198.18.0.0/15` answers stop the comparison before
product calls. Use an environment with real public DNS answers; do not disable
SSRF guards, rewrite selected URLs, or classify this environment rejection as a
product retrieval failure. Full native tool/worker envelopes are retained privately
with hashes, separately from scored answer text. Historical outputs without those
envelopes remain insufficient for an exact raw-response audit.

When those retained bytes contain private host paths or credentials, the shared
`publication_privacy.project_evidence_view` creates a separate post-score copy
under `public-evidence/`. Keep the originals private. The bound receipt records
original and public hashes and byte sizes; `require_public_evidence_view` checks
the delivered copy without claiming it can reconstruct omitted values. Native
JSON envelopes are projected structurally to preserve field types. Before using
such views in a handoff, verify that the public native-field projection still
matches the public answer and that rescoring it preserves the task verdict.
Do not replace an original scored binding with a public-view hash or describe a
redacted reproduction as byte-for-byte recovery of the private call. Public
frozen presentations declare `publication_evidence_views` in their presentation
binding. The shared gate requires complete current answer/native coverage,
checks the actual displayed aliases, rejects private artifact trees, and keeps
ordinary acquisition/aggregation checks strict. Redacted excerpts can verify
visible text, not original private coordinates. Report rebuilding uses saved
original scores; changed public text does not reproduce every original text
quality diagnostic. Disk-backed metadata projection and independent file clones
keep large histories practical without hard-linking writable outputs to inputs.


For native caller comparisons, `dns_mode` is explicitly `system` (default) or
`public_doh`. All six local methods (Moli, Crawl4AI, OpenClaw, DeepSeek, CowAgent and
LangChain) must declare the same mode
when selected together. `public_doh` uses the fixed
[Cloudflare JSON DoH endpoint](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)
for every hostname and redirect, not a case allowlist or failure fallback.
Native Chrome uses the same endpoint with its DNS wire-format client; other
callers query A and AAAA through the JSON client. DNS selection does not change
the shared [retry policy](#retries); DNS time counts inside each call's deadline.
Python resolution is scoped to the calling thread and restored;
Node uses the products' resolver interfaces. Native private-address checks and
address pinning remain active where the product provides them. CowAgent's native
SSRF setting is preserved (the pinned implementation defaults to disabled).
Moli on Darwin uses a process-local native resolver bridge. Build it explicitly
outside Git with `uv run --frozen python -m harness.web_fetch native-dns
~/.browser-eval/toolchains/native-dns/libbrowser-eval-dns.dylib`, and configure
`native_dns_library` (or `MOLI_NATIVE_DNS_LIBRARY` in the example). The adapter
checks the library hash, compiled C source hash and architecture before calls;
a changed compiled source requires rebuilding. The Python coordinator remains
covered by the run's runtime-source hash. It never compiles during a measured
call.
Crawl4AI's browser route starts the configured Chrome channel with a temporary
strict-DoH profile and connects through its native CDP interface. The original
native launch arguments are preserved; there is no system-DNS fallback for
page hosts. The fixed DoH endpoint itself uses system DNS for bootstrap. Browser
startup and DNS remain inside the outer call deadline. Non-browser Crawl4AI
file/PDF routes explicitly reject this mode. No global DNS setting is changed.

A public-DNS configuration must bind `src/harness/web_fetch/workers/public_dns.mjs` for the Node
workers, `src/harness/web_fetch/network_dns.py` for CowAgent/LangChain/Crawl4AI, and DeepSeek's
native `src/network.ts` in `required_paths`, using the normal `{harness_root}`
and `{repo_path}` tokens. The preflight checks all selected case hosts with that
resolver before product calls; mixed modes, failed resolution or synthetic answers
stop execution. The system DNS mode continues to reject synthetic answers.

### Native answer fields

DeepSeek's public output schema declares `value.body.kind` and
`value.body.content`; `content[].text` is its model-facing render. The adapter
passes the declared HTML/text body verbatim to shared scoring and retains the
rendered text and full tool envelope privately. An empty declared body stays
empty; a missing body is an invalid response. It does not reconstruct content
from UI messages, logs, scripts or later requests. Binding
`src/harness/web_fetch/workers/dsh_output.mjs` is required for this projection. Old calls without the
raw tool value cannot be retroactively reconstructed or rescored as this route.

Fixed-native calls pass the full caller budget to HTTP clients (45 seconds is
45,000 milliseconds) without overriding product-side timers. Ordinary adapters
use the shared inner budget, reserving time for completion within the outer
deadline. The outer deadline covers input through answer processing. Late answers
are discarded; an uncancellable request still running isolates that method and
records the unexecuted scope. Do not abandon requests concurrently or add answer time.

The DNS fairness precheck covers all six local methods with one declared mode,
`system` or `public_doh`. On Darwin, Moli uses a source/hash-bound in-process
resolver for `public_doh`; Crawl4AI body-browser calls use the selected Chrome
binary and a separate secure-DNS profile. System mode still rejects synthetic
addresses. Do not mix modes or disable product safety checks. Fixed-native Moli
calls retain the default internal HTTP timeout and predeclared native response
capacity. Crawl4AI retains its complete native `model_dump` response, prioritizes
declared `redirected_url`, and scores native `raw_markdown`.

### Credentials and response metadata

Evaluation does not inherit API keys, tokens or account IDs from the parent
process; they come only from the repository `.env`. Ordinary runtime paths may
come from the calling environment; an external `--env-file` cannot supply
evaluation credentials. Crawl4AI prioritizes native `redirected_status_code`
while retaining the original status. Native `links.internal`/`links.external`
name/URL pairs enter shared link input directly; names are not reconstructed from
logs, requests or bare URLs.

### Named records in Markdown tables

A `named_link` task can declare `markdown_title_column` and
`markdown_link_column` in its `entry_scope`. These are exact table headers from
the independent source. A visible name and one detail link in the same row form
a record, including when a native converter renders a JavaScript-backed name as
plain text. The existing host, path, identity and minimum-count checks still
apply. Missing or repeated declared headers, empty names and multiple detail links do not
supply records. Content in another row or a fenced example cannot fill a missing
field.

### Output and download limits

Moli's `max_bytes` bounds the captured answer. Page and subresource downloads use
Moli's native capacity policy; the adapter does not pass this answer limit as
`--http-max-response-size`. A large script can produce a small answer, so limiting
its download would change page execution. Answers above `max_bytes` fail with
`output_too_large`; truncated answers are not scored.

### Moli native family completion

For native dynamic-body tasks, Moli requests `--layout`, `--with-frames`, and
`--wait-until domstable` for the whole family. Markdown comes directly from
Moli; the harness does not assemble frame content or refresh DOM geometry.

A Moli provider configuration can override completion with
`wait_until_by_track`, for example `{"risk_control": "domstable"}`. Keys must
be registered task families and values must be supported native wait modes.
The policy is declared before calls and applies to every task in that family;
case IDs, hostname rules, answer-dependent delays, and extraction scripts are
not accepted by this setting. Call metadata records the selected family policy.

For a complete-category comparison, Moli also accepts `native_output: "auto"`
or `"html"`. Automatic output omits `--dump` and uses the declared wait policy,
including for direct files. HTML passes `--dump html` and the declared wait
policy for text and resource tasks; file tasks retain raw delivery without
those flags. Automatic output does not promise document text extraction.
Its saved `ordinary_html` interface identifier is a historical request-route
name, not evidence of the returned format. Automatic calls record
`requested_output: auto`; text output is `unknown` until decoded, then
`utf8_text`, without guessing HTML versus Markdown. Raw-file delivery remains
`original_bytes`. `output_format_source` records the basis for that label.
Omitting this setting preserves the original native routing policy.

Cloudflare accepts a nonempty `scrape_selectors` list to use the same `/scrape`
request for every task in a cohort. For example, a media cohort can request
`body`, `img`, `picture source`, `video`, `video source`, `audio`,
`audio source`, `iframe`, and `a[href]` together. Freeze these options before
calls and compare complete categories; never choose settings from task answers
or replace only improved cases. Retain unsuccessful candidates as evidence.

### Fixed native calls

`uniform_native_parameters: true` freezes class-level interfaces, answer fields,
and request parameters before calls. Each task declares `input_media_kind`
independently of its URL spelling or reference answer. The 45-second caller
budget leaves product timers at their native defaults. Per-case extraction hints,
cross-provider fallback, injected extraction algorithms, and answer synthesis are
prohibited. The exact dynamic marker `{"version": 3, "mode": "native"}` declares
no per-case assistance and is allowed with a stable acceptance contract.

Moli's fixed-native request builder uses Markdown for webpage text, HTML for
resource discovery, and original bytes for declared direct PDF, DOCX, and file
inputs. Its ordinary native task-family interface supports native Markdown with
layout and child documents. The invocation profile declares which interface was
measured; a newer implementation never relabels historical calls. Output capture
limits do not cap underlying downloads. Caller retries follow the shared
[retry policy](#retries), while historical cohorts retain their frozen budgets.
Keep the complete native response, projection declaration, and request receipts.

### Content acceptance and page identity

Final URLs and document-language metadata are diagnostic by default. A task that
requires these observations declares `page_identity_required: true`; its existing
navigation/language fields define the accepted identity. Missing identity stays
unavailable unless another independently failed requirement establishes failure.
A proven mismatch fails that task. Native response provenance is always checked,
including the title and final URL supplied to the scorer, regardless of whether
identity itself is an acceptance requirement.

When migrating a historical task that already required page identity, make that
requirement explicit through a bound offline revision. Preserve its original
outputs, timings, review observations, and prior task definition. Adding this
annotation does not authorize dropping any other task requirement or creating a
new provider call. Content requirements continue to reject missing target text,
records, fields, empty answers, or error-page shells independently of metadata.

### Main-document identity on declared navigation tasks

Legacy debug GET completion rows do not bind redirect request owners or Location
headers. Their order, a challenge query hint, or the first completion after a
navigation commit cannot prove a final document URL. Unassociated redirects are
unavailable, while a non-redirect response matched to the requested navigation
can supply its explicit final URL. Historical logs and outputs remain immutable.
The changed route requires a new affected benchmark execution before claiming
new scores or timings; local fixture validation is not a benchmark rerun.

## WCXB paired timing and publication

WCXB builds read profile-bound files from `<evidence>/report-assets/` and preserve the same relative paths in the public package; restore missing files from the profile's pinned Artifact Site version, never from an unversioned latest report. Do not silently fall back to a repository copy.

For the current WCXB report, the source-qualified manifest is the entire evaluation population for quality, delivery, and latency. Keep source-invalid cases outside the current manifest, scored evidence, public package, and future provider calls; preserve earlier physical calls only in private historical evidence. The dataset page owns frozen task inputs, source URLs and human reference scope; it must not show measured browser scores. The home page owns aggregate comparisons, the result analysis owns the filterable method-case inventory, and case result pages own full responses. Keep a direct link from each dataset row to a safe frozen-page preview and its human reference. Default result filters must show a nonempty, clearly labeled diagnostic population when one exists, and filtered tables must shrink rather than leave a large blank viewport.

The WCXB command uses the shared English fallback. Set
`BROWSER_EVAL_REPORT_LANGUAGE=zh` or use `--language zh` for a Chinese-only build; its English entry shows a not-generated
notice. Bilingual release checks require matching routes and values. English
repair content must be present in the bound asset set for an English build.
See [report languages](../../../docs/report-hosting.md#report-languages) for all builders.

WCXB findings live in the external, hash-bound `report-content.json` guide editions.
The home page summarizes measured values and links to that interpretation; it must
not duplicate case-specific findings in Python. Release checks verify the bound
article, current profile populations and participant rows. Optional-feature receipt
paths come from the content binding, and their case must belong to the current
population; the checks do not require a particular outcome. Illustrations and
examples are validated against the supplied article rather than a fixed inventory.

WCXB display routes use recorded interface metadata and the shared method registry;
registered request examples are labeled separately from measured routes. Missing
route evidence stays unavailable. The builder, renderer and release checker reuse
`wcxb_scoring.paired_latency_methods` to join comparison roles through the profile's
binary hashes and certified identities. Participant names are not role bindings;
a missing or ambiguous binary match is rejected. Version aliases require complete
participant evidence, not another renderer entry.

`wcxb_latency.py` owns paired-call collection, receipt validation and certification.
The collector uses the profile's complete qualified population, timeout and binary
hashes. It runs two serial rounds, reverses order per task and selects the final
round before observing results. Failed calls remain in the physical log; an
incomplete, drifting or invalid cohort receives no valid receipt.

```bash
uv run --frozen python -m harness.web_fetch wcxb-latency \
  --profile /absolute/path/current-report.json \
  --dataset /absolute/path/wcxb --manifest /absolute/path/qualified.json \
  --release /absolute/path/released-binary --candidate /absolute/path/candidate-binary \
  --output /absolute/path/new-paired-evidence
uv run --frozen python -m harness.web_fetch wcxb-latency certify \
  --profile /absolute/path/current-report.json \
  --dataset /absolute/path/wcxb --sample-manifest /absolute/path/qualified.json \
  --evidence-root /absolute/path/new-paired-evidence
```

The historical command defaults to the Moli adapter. Other implementations use
`--config` with exactly two `providers` entries, `release` and `pr`, using the
normal Provider configuration fields. The binary flags bind those roles to the
profile's release and candidate hashes. Certification requires the receipt hash
bound in the experiment's profile; it never changes a historical profile or
replaces an existing certificate.

The maintained WCXB builder writes `public-files.json` after finishing its staged
report. Release checks and packaging reject missing, changed or undeclared files.
Older generated trees must be rebuilt from their frozen evidence. After the
WCXB release and visual checks pass, the publisher can be invoked from an
installed package with an explicit repository authority:

```bash
uv run --frozen python -m harness.web_fetch wcxb-publish \
  --profile /absolute/path/current-report.json \
  --authority-root /absolute/path/browser-eval --source /absolute/path/wcxb-report
```

The publisher freezes the package and profile, checks the expected remote version,
verifies exported file bytes and the public share page, then advances only the
profile's live publication version. A failed export check retains the uploaded
version in a private operation journal so verification can resume without
uploading the package again. Publication work files use the configured data home.

## Reference candidates

`reference_freeze.py` owns candidate preparation and reviewed promotion as well
as reference freezing. `scripts/run_certified_benchmark.py` runs freezing and
postcheck through it. Promotion has no separate script; installed callers can
inspect both interfaces without a repository checkout:

```bash
uv run --frozen python -m harness.web_fetch.reference_freeze prepare-promotion --help
uv run --frozen python -m harness.web_fetch.reference_freeze promote --help
```

Supply the dataset, independent captures or browser observations, new candidate
and receipt paths, approval identity, concrete root cause and paired regression
IDs. Promotion recomputes the candidate from those observations and rejects
changed bytes or a mismatched approval receipt. It cannot edit a pinned input
installation. Use explicit `--research-root` and `--sources-root` when promoting
in an external authoring workspace; these operations do not call providers or
start an independent review.


## WebMainBench sample evidence

The sample and full-cohort entries use `webmainbench_assembly.py`. A sample's
profile declares its methods, source hash, sample-manifest hash, scoring commit
and execution limits. The manifest declares the parent population and selected
tasks; the assembler does not infer a population from the available shard files.
Sample execution is serial, with one logical result per method and task.

`uv run --frozen --extra webmainbench-eval python -m harness.web_fetch webmainbench-review` is an explicit deterministic audit entry. It
checks saved evidence without calling a provider or importing the official
scorer for its independent text, code and formula checks. Its receipt binds the
profile and every reviewed record, output, summary, execution record and fixture
certificate. Applicable metric failures count as zero; a metric explicitly marked
as reference-inapplicable is excluded. This audit does not substitute for a human
result review.

After an explicitly requested audit has produced a passing receipt, assemble it:

```bash
uv run --frozen python -m harness.web_fetch webmainbench-assemble \
  --source-root /absolute/path/provider-shards \
  --dataset /absolute/path/WebMainBench_545.jsonl \
  --sample-manifest evaluation/public-webmainbench-200.json \
  --profile /absolute/path/report-profile.json \
  --review /absolute/path/passing-review.json \
  --output /absolute/path/new-assembled-evidence
```

The output must be new and outside the input evidence. Assembly retains every
physical-call output, uses the final declared response, verifies hashes and
exposes the directory only after the entire matrix passes. Unbound review
receipts cannot authorize a new assembly. Existing published reports retain
their frozen evidence and are restored from their pinned release.

### Frozen local report inputs

Keep each saved report's presentation binding and publication target outside Git,
alongside its evidence under `BROWSER_EVAL_DATA_HOME` (default `~/.browser-eval`)
or another explicitly selected directory. These are report instances, not reusable
code. Use the binding supplied with that exact snapshot; do not select an older
binding by its task count or treat a historical publication target as current.
The binding records the dataset, results, scorer and auxiliary evidence hashes.
The shared report owner validates those saved inputs without provider calls or
rescoring with the installed scorer.

For an existing frozen local report, pass its external binding explicitly:

```sh
uv run --frozen python scripts/check_report.py /absolute/path/to/saved-report \
  --presentation-binding /absolute/path/to/saved-report/presentation-binding.json
uv run --frozen python scripts/build_report.py --local-audit \
  --evidence /absolute/path/to/saved-report --output /absolute/path/to/new-report \
  --presentation-binding /absolute/path/to/saved-report/presentation-binding.json \
  --language zh --include-english
```

The build copies the validated binding into the new report. Check the new output
with its matching `--presentation-binding` too. For a fresh local measurement,
use `--local-audit` with its external evidence and an explicit output directory;
do not borrow a previous report's binding. Any scoring change must first follow
the declared correction protocol; rendering alone does not update scores.
Publication remains a separate explicit action with that report's own target.

The maintained synthetic example in
`tests/test_local_audit.py::test_new_local_matrix_is_complete_and_keeps_logs_without_claiming_review`
exercises collection, external bindings, rebuilding and packaging without real
provider calls. It also checks changed evidence and preservation of frozen scores.
`tests/test_external_local_report.py` additionally runs the public build and
check commands with different external task/method inventories and deadlines,
including both language editions. Its inputs stay in a temporary evidence tree;
no repository profile is created.

The local measurement schema is a single-call protocol: one run, no retry and
serial execution per provider. A different deadline or supported DNS mode is an
input, not a new protocol. Repeated experiments need a predeclared score-bearing
run and complete physical-call lineage before they can share this report's
one-result-per-method/task denominator. Do not enable repeated runs by removing
the schema checks or by selecting the best observed result.

Historical audit tests read their frozen scorer bytes from
`<data-home>/scoring-snapshots/<scorer-sha256>/`; they verify the complete scorer,
registry and scenario-test hashes rather than substituting current code.
Ordinary tests skip absent historical snapshots; strict reference-input tests
require them. Changed installed bytes fail either mode.

### Evidence ownership and historical readers

LexBench scores submitted answers and delivered file bytes. Native response
selection, diagnostic log decoding and source-field verification belong to
`native_projection.py` in this harness. Both collection and saved-answer checks
use that owner; changing the owner does not upgrade historical calls to a
pre-call policy they never recorded.

All hash-bound reads use `publication_privacy.read_evidence_binding`, including
proposed files during a local revision. The revision supplies a scoped resolver;
the reader still rejects unsafe paths, changed bytes and undisclosed public
substitutes. Audit and privacy readers do not import the revision writer to read
evidence. Canonical compact JSON hashes share `harness.record_io`; raw-file,
pretty-printed manifest and normalized-text identities retain their own byte
contracts.

New local revisions reuse page-identity opinions only when task, physical-call
and answer bindings remain unchanged. Changed inputs require an explicitly
bound new identity review; the revision never migrates an old opinion to a
changed task automatically. Legacy diagnostic-field rebinding receipts and
resumed/admitted execution plans remain read-only decoders for frozen inputs.
There is no current writer for those historical formats. Do not extend them
into a new execution protocol or infer a new independent review from them.

`local_execution_legacy.py` owns only the historical plan decoder and its
dispatch. `local_audit.py` retains the older, explicitly selected merged-audit
receipt reader; new local matrices select `uniform_native_local_measurement`
before that reader. These readers remain supported because frozen evidence
still names their contracts. Removing them requires migrating the corresponding
authority to a pinned, reproducible verifier first, not merely moving Python
files into an untrusted evidence package. Their negative controls must continue
to reject missing calls, changed configuration and altered review evidence.

### Current acceptance and retained corrections

The shared scorer uses one current acceptance predicate. Verification/error hints
are diagnostics; declared `forbidden_any` requirements remain contract gates.
Treating previously forbidden words as hints requires an explicit case revision
to `diagnostic_any` and a complete rescore of the affected method/task scope.
Content, records, page identity and completion deadlines still determine acceptance;
reference precision and observed challenge components remain separate diagnostics.

Historical results retain their hash-bound frozen scorer rather than selecting
old behavior in the current runtime. The current predicate rejects legacy metrics
that need that frozen scorer or an explicit offline rescore. Current regression
controls verify current behavior; superseded controls retain their IDs, definition
hashes and retirement reasons, with original definitions preserved in the frozen
history. Do not mix old metrics with a new predicate.

Stable text contracts declare `content_scope` (`target_content` or
`full_page_stable_coverage`) and may declare `text_exclusions`. A target-content
contract rejects positive anchors observed in excluded navigation/footer regions.
A whole-page contract can require their stable text, alongside body coverage or
complete record requirements. `source_location` records the independent observation;
it is not an implicit DOM selector. HTML exclusions apply before text matching;
plain-text outputs need explicit textual exclusions where roles otherwise disappear.

The following are Python maintenance APIs, invoked explicitly from a Python
script with `uv run --frozen python <script.py>`; they do not have dedicated CLI
commands. Import them from `harness.web_fetch.local_revision` or
`harness.web_fetch.publication_privacy` as named below. Their arguments are
caller-supplied evidence paths, bindings and correction configuration, not values
inferred from the latest report. Use them only for an authorized correction or
history reduction.

`local_revision.revise_local_snapshot(source, configuration)` only rescores a separate saved snapshot.
Changed contracts require `rule_revision: true`. Its receipt binds the predecessor,
selected complete method/task scope, regression IDs, source hashes, explicit contract
changes, actual scoring time and verdict changes. Physical output bytes, call identities,
timestamps and latencies remain unchanged. Proven environment defects exclude a
case symmetrically across every method. The result ledger derives all transitions
from per-attempt states; overridden review opinions require task/answer hashes,
original answer excerpts, concrete rules and a reason. This is supplemental
adjudication, not a new blind review of the entire matrix.

For explicitly requested history reduction,
`local_revision.compact_local_snapshot(source, binding, **options)` validates the
bound revision chain, installs a current-only authority and returns its path/hash
binding. It retains the current measured evidence; it is not a report renderer.
The lower-level `local_revision.compact_revision_authority(source, binding, ...)`
returns a validated current-state authority document. It does not install that
document or remove history; callers must persist and bind it before discarding
superseded files. Prefer `compact_local_snapshot` for the complete operation.
For a public metadata copy,
`publication_privacy.project_host_metadata_files(files, host_prefixes, **options)`
accepts a relative-path-to-bytes mapping and explicit host prefixes, returning a
projected mapping plus an original/public hash receipt. The caller persists and
binds that receipt; scored answers and raw response payloads cannot be changed
through this metadata operation.

Local uncertified reports retain a disclosure on every page and omit ordinal
rankings. The CLI cannot hide the disclosure. Historical article HTML is limited
to inert allowlisted markup and safe links/raster images; its translations apply
only inside the article. A diagnostic snapshot cannot certify a formal release.


### Native scored result context

Native projection schema 2 includes `result_fields.title` and `result_fields.final_url`
alongside text, resource metadata and delivered files. Validate these fields before
persisting a call, before native-response scoring, and when aggregating saved calls.
JSON fields use declared structural pointers. A final address may also be replayed
from a retained same-call main-document log or a source HTTP response receipt;
request-URL fallback applies only when the selected native address is absent.
Resolve relative resource attributes against that verified address. A title absent
from the native response cannot supply required facts.

Schema-1 declarations must not contain `result_fields`, even an empty object.
Current persistence rejects mixed declarations, including discarded timeout answers.
Immutable schema-1 acquisitions retain their original text/resource/file scope;
they do not retroactively prove title/address provenance. Bind a separate offline
context audit to historical results, replay the complete affected route when a
context difference is found, and retain the original records and exact score effects.
These checks add no provider calls.

Native route selection, per-browser interfaces and the route gate are in the
[live-URL guide](HEADLESS.md#native-browser-routes).

### Resource comparison scope

The primary statistics in `resource_cohort.py` keep the predeclared complete method
and task matrix, including failed calls. Mean, P50 and P95 use the same scope, and the
report also shows each method's pass count. Statistics over each method's successful
calls are stored separately as a conditional distribution. Tasks that all methods
passed are an additional diagnostic only and must not be used to select ranking
samples. When there is no common success, the complete-call resource statistics must
still be kept; a conditional metric with no successful items is unavailable, not zero.

To correct the statistics scope, re-aggregate from saved, hash-verified measurements.
Do not call browsers again and do not overwrite the old aggregate. Primary results,
raw outputs and per-call resource counts stay unchanged. Release checks verify all
measured values, denominators, metric directions and the unique lowest-value marker;
ties stay neutral. Resource consumption and task pass rate are presented separately
and are not combined into a total score.

### Reviewed browser interpretation inputs

A current browser interpretation can require a complete failure diagnosis by setting
`require_failure_review: true` and binding `failure_review: {path, sha256}` in its
external `webfetch_browser_interpretation/1` content. The bound file uses
`schema: webfetch_failure_review/1`, binds the exact `results.jsonl` bytes with
`results_sha256`, and contains one `failures` entry for every failed result and no
passing result. Each entry identifies `attempt_id`, `provider` and `case_id`, a
stable lowercase `category`, a reader-facing `category_label`, an evidenced
`reason`, a `remediation_status`, and nonempty hash-bound
`evidence_paths: [{path, sha256}]`. A program error type remains a separate
classification; it does not establish the reviewed cause. The build, report check
and publication boundary reject stale, incomplete, duplicate or mismatched reviews.

The same content may supply `background`, raster
`figures: [{path, sha256, alt, caption}]`, and measured
`case_examples: [{provider, case_id, explanation}]`. These use the existing report
builder and its six-chapter interpretation. Assets stay outside Git and are copied
only through their hash bindings. The independent HTML embeds figures and reviewed
causes without copying every failure's complete output and logs.

### Sharing a frozen diagnostic snapshot

Pass the same `--presentation-binding /path/to/binding.json` to
`uv run --frozen python -m harness.web_fetch site-package` and `site-bundle-check` when
sharing a previously validated frozen local report. Both commands verify its
saved matrix, scorer identity, evidence and revision bindings through the existing
presentation validator. This does not certify the cohort or add result reviews;
its visible evidence-state and review-scope disclosures remain intact. Without
this explicit binding, publication retains the certified-evidence gate.

The profile-bound publisher accepts the same binding. Use `--visibility unlisted`
for ordinary link sharing at the existing `/s/<slug>` address; `private` and
`public` must follow the requested access scope. The publisher verifies the
actual access state without creating an independent share link.
`uv run --frozen python -m harness.web_fetch site-verify` checks a published
report's metadata and, with `--export`, its bytes against the expected archive.

Large publication trees automatically use content-defined deduplication and a
compressed catalog when fixed-block storage exceeds the upload budget. Both
encodings retain complete virtual files, source bindings and file hashes.
An acceptance contract stays inline when no standalone contract file exists.
To reuse a completed public content cache, pass `--cached-content <directory>`
to `uv run --frozen python -m harness.web_fetch site-package` with the same source and, when applicable,
`--presentation-binding`. The packer refreshes source-bound result privacy and
footprint projections when the cache uses an earlier representation; it rejects
a cache bound to another source. `--refresh-privacy` additionally applies the
current privacy checks to other cached files. Complete publication checks run
before the archive is installed. Source and input cache remain unchanged. A cached
representation passing a secret scan alone does not prove it matches the current
export contract, and upload limits never justify dropping evidence or a separate renderer.
Saved public snapshots may retain original scored hashes while storing separately
bound public evidence views. Verify both identities through the existing public-view
helpers; report retained views separately instead of calling them original bytes.

If the complete compressed report still exceeds a deployment's artifact capacity,
use `--parts-directory <new-directory>` to prepare a main archive and capacity-bound
data attachments. The preparation gate checks their combined evidence, links and
hashes. Publish the attachments, bind each exact committed version with
the Python API `harness.web_fetch.report_package.bind_data_parts(archive,
plan_path, publications)`, and pass `--parts-manifest <directory>/parts.json` when publishing
the main report. Ordinary publication rejects unresolved or mismatched attachments.
Keep the main profile's `data_publications` registry aligned with that bound plan;
main and data artifacts must use the same profile-bound service. Keep one report entry
and include the attachment archives and target registry in the main publication snapshot.
Invoke that API from a maintenance script with `uv run --frozen python <script.py>`;
there is no separate bind command. `archive` and `plan_path` are `Path` objects
for the prepared main archive and parts manifest; `publications` maps each part
identifier to its committed publication receipt. This step binds existing uploads;
it does not upload attachments.

## Saved multi-suite reports

Use `scripts/build_report.py --saved-matrix /absolute/input-spec.json --output /absolute/new-report --language zh` to display saved results across suites.

Final presentation requires verified settings for every displayed method/family,
including unchanged and completion-only groups. Request options must be uniform
within each declared input class; DNS, timeout and retry budgets must also agree
across methods in a family. Unknown settings and adapter-rejected positions block
the final view. The methodology table exposes settings and collection dates;
equal settings do not establish a shared measurement window. Post-outcome
parameter selection and changed task or acceptance requirements belong in separate audit reports,
not the primary original-contract ranking. Saved records are never rewritten to
make historical cohorts comparable. Audit reports show scores by default. Set `ranking: "saved_task_pass_rate"` in
the external saved-matrix specification to request overall and family ranks.
Each ranked table requires one selected result per method/task, complete coverage
of the same task population and one recorded scorer identity; otherwise it retains scores with a visible reason
for withholding ranks. Exact ties share competition ranks (1, 1, 3), preserved
when sorting other columns. Configuration-selection and recorded-rule limits
remain disclosed; descriptive ranks do not upgrade certification. Ranking
tables use final-call latency; accumulated retry cost stays in the evidence.

- The hash-bound specification declares datasets, providers, selected results and evidenced missing outcomes. Suite-qualified identities preserve original cases and scores. No provider calls, rescoring or certification are added.
- This mode uses the existing Web Fetch renderer, including datasets, method pages and result/reference comparisons. Missing outcomes have a separate inventory. Keep the HTML pages and evidence directories together.
- Optional `report_families` lists ordered `{id, label}` entries for every observed task family. It groups the presentation by task content (including embedded media versus direct files), while preserving suite-qualified evidence identities and all saved scores.
- A family may set `task_family` and `source_suites` to keep independently frozen populations separate. Selectors must partition all cases exactly once. Set `combined_ranking: false` when cross-population aggregation is not meaningful; the summary then shows per-group results without a combined pass-rate column or rank.
- Optional `offline_revision` binds a revision `root` and a path/hash `receipt` produced by the existing local revision owner. Its complete predecessor cases and results must match this matrix. The report preserves original rules/results beside revised scores and labels the offline change. Optional `review_files` maps attachment names to JSON path/hash bindings; no provider calls occur during assembly.
- To fill previously uncalled positions after an offline revision, add new evidence to `sources`, remove the corresponding `missing` entries, and bind `completion.baseline_matrix`, `completion.baseline_audit`, and `completion.contract` by path and hash. The baseline audit binds the complete population and retained case/result bytes; unrelated calls and tasks cannot change. Separately verified offline revisions and whole-group parameter selection remain explicit exceptions. The pre-call contract declares `created_at`, `provider`, the total `cases`, and `jobs` with `suite` and `case_ids`. Every declared result must be present, newer than its contract, and previously uncalled. The frozen revision retains its original call population; completion cannot replace those calls.
- Multiple completion rounds use `completions`, an array of the same bindings with disjoint scopes. A shared-suite contract may declare `providers` and `case_ids`, with `suite` on the completion entry. An optional `note` appears on each completed result detail; it must describe the bound evidence, including any historical DNS difference; disclosure cannot waive uniformity checks.
- `excluded_report_families` removes complete named families from report rows, pages and statistics while retaining the full bound source population and historical parameter-selection scope. Exclusions must be unique known family IDs and cannot remove every family.
- `excluded_report_providers` similarly removes whole methods from the report while preserving the complete original scoring receipts. Other methods retain their selected records; rankings and totals are recomputed on the reported population. Unknown, duplicate, or all-method exclusions are rejected.
- All supersessions, including uncommitted replacement calls, require a hash-bound `execution_contract` on the scope or individual cell. The loader checks the authorized native settings and complete retry sequence before admitting a replacement. Uncommitted replacements also require observed configuration/timeout/call-count fields and a hash-bound `execution_log`; each `http_request_started` event identifies the provider, case, start time, configuration digest and timeout. Missing observations or inconsistent logs reject the replacement without inventing scores or elapsed time.
- `adoptions` binds a saved-matrix specification and `user_selected_complete_report_groups` authority for explicitly selected provider/family pairs. It replaces each complete visible group regardless of pass count, retains the previous calls as history, and verifies task/scorer identity. It cannot overlap parameter candidates or another adoption. Bind `execution_contract` for a shared execution policy, or disjoint `execution_contracts` entries with `suite`, `provider`, `case_ids` and `contract` covering every adopted call. Native settings and retry chronology are checked against the original acquisition records; later offline scoring does not rewrite historical retry decisions.
- Parameter comparisons require matching scoring provenance, the authorized provider configuration, actual native request receipts, and complete failure-retry sequences. Different scorers require a separate explicit offline revision before comparison. Replacement scopes and split retries must have valid pre-call chronology. The portable checker rejects unlisted files and symlinks, including additions beneath evidence directories.
- For displayed-category comparisons, set `parameter_reruns.selection` to `replace_family_if_pass_count_improves`. Candidate completeness and pass counts use the same `report_families` partition as the report, including families spanning execution tracks. Verified native settings must be uniform within each eligible group; ineligible groups cannot win by score. Each family is selected independently and ties retain an eligible baseline. Historical `replace_track_if_pass_count_improves` contracts retain their original track-wide population; changing that frozen selection requires an explicitly bound selection amendment.
- Adoption also compares verified native request settings, DNS, timeout and retry allowance within each displayed family, including retained retry calls. Numeric values such as `30` and `30.0` are equivalent. Method- or case-specific `configuration_exceptions` are rejected: an authority cannot relabel different settings as equivalent. Historical differences remain in the evidence but cannot qualify a mixed group for uniform comparison. Individual cases cannot be selected by their score.
- Check portable report files with `scripts/build_report.py --check-saved-matrix /absolute/new-report`. Audit reports support this portable check. Final selected results must share one valid saved scorer identity after any validated offline revision; mixed or missing identities remain diagnostic. Final reports require `--source-spec /absolute/input-spec.json` and original native evidence; only the source-validating builder can authorize final rendering, never caller-authored configuration summaries. Source paths are resolved beside that external specification; the report audit contains only names and hashes.
- Keep complete suite definitions when selecting methods and bind a receipt for every skipped cell. Retain physical retries for each selected logical result. Current-only selection does not establish why earlier results were replaced; corrections still require disclosure of scope and score, denominator and latency effects. Instance specifications, captures and HTML remain outside Git.

## Report method metadata

Certified Web Fetch reports keep each registered provider's interface example and
adoption data. `research/provider-adoption.json` holds the public-code adoption statistics. Refresh
them with `uv run --frozen python -m harness.web_fetch.provider_adoption search`, or
re-verify the known candidates with its `refresh` command. Both need an authenticated
`gh`; the search checkpoint stays under the research evidence directory.

`data/report-methods.json` is the shared, packaged catalog for product labels,
method profiles, adapter contracts, interface examples, access requirements,
and capability sources. Keep Chinese and English author text together as
`{"zh": "...", "en": "..."}`. A versioned execution participant resolves to its
recorded engine or the engine in `configs/providers.headless.json`; adding a
release of an existing engine does not require another catalog entry.

Saved `reported_version` takes precedence over hash-bound implementation
provenance and other recorded version fields. A native engine's four-part CDP
compatibility version is not an executable release. Missing metadata remains
unavailable; an unregistered method retains its outputs and explicitly reports
missing descriptive metadata instead of crashing the report.

Interface requests in this catalog are labeled examples. Saved public call logs
own measured request fields; frozen run contracts own budgets and method scope.
Do not copy a cohort's task count, timing, or result conclusion into product
metadata. Source verification dates belong to each source entry, not to a global
report date. `checked_version` binds a source to the product version that was
actually checked; use `null` when that identity was not recorded. Reports disclose
missing or mismatching version coverage without changing measured results.

Each method has one `access` object (`type`, `account`, `configuration`, `links`,
and `env_vars`). Hosted checklist entries may additionally declare `priority`
and `required`. Entries with `status: planned` document account preparation but
are not executable providers and are not added to reports unless measured.
Product documentation URLs belong to the method catalog. Execution participants
may have distinct labels and an explicit `release_channel`; they do not override
the product documentation source. Saved binary hashes and call records own build
provenance. A shared adapter is a transport, not necessarily one product: when
multiple products register an adapter type, the record must name its engine.

`observed_routes` matches saved metadata to descriptions; `interface_variants`
selects request examples from that same evidence. An adapter's `interface_method`
selects its catalog interface owner. `runtime_parameters` matches recorded policies
to parameter descriptions; optional numeric fields must be positive and finite.
`task_interfaces` describes planned routes and waits. The execution selector
`output_mode_for_case` owns the mode; descriptions do not redefine it.
Measured call routes and waits override those examples, including missing values.
`task_capabilities` describes the selected route boundaries without turning task
success into a product capability claim. Run budgets, delays and retry limits
come from execution manifests, not this catalog.

Read colocated translations by language and field identity. Keep both languages
in the owning object rather than copying them into legacy translation maps.

Historical WCXB and WebMainBench entry points remain supported. The legacy
75 + 25 audit reader validates its explicitly selected frozen evidence contract;
new matrices use `uniform_native_local_measurement`. These are not unused code
merely because their contracts describe older evidence.

### Adding a method or release

1. For another release of an existing engine, add or select its execution entry in
   `configs/providers.headless.json` (or an external provider configuration), bind
   its executable and expected identity, and collect new evidence. No new product
   description or renderer branch is needed.
2. For a new product, add its bilingual presentation and account requirements to
   `data/report-methods.json`. A method may `extends` a named `templates` entry;
   dictionaries merge, lists replace, and missing or cyclic parents are rejected.
   Register its recorded route descriptions and native output contracts.
3. Configure its executable provider separately. Reusing a transport does not
   automatically add a new browser implementation to `providers/headless.py`:
   a new engine needs execution support and tests there. Existing supported
   adapters can use an external provider configuration without another adapter.
   New task modes belong in the execution selector; catalog rules only describe
   the resulting route and any task-specific limitations.
4. Run the method registry tests and the public build/check commands against a
   synthetic matrix containing the new participant, then run `make ci`.

Catalog JSON alone supplies presentation, not executable integration. Adapter
`interface_method` chooses a request-example owner; method `adapter_types`
resolves product identity only when unambiguous. Known old participants may
resolve through the execution registry without recorded `engine`; an unknown
participant sharing an ambiguous adapter must record its engine explicitly.

Execution configuration `source_url` no longer owns documentation links. Move
that value to the product's catalog `source_url`; old external configuration keys
are ignored for report links. Binary hashes and saved call receipts remain the
source of execution provenance.

Hosted examples describe registered defaults, not universal measured settings:
Tavily `extract_depth` defaults to `advanced`, Exa `text_verbosity` to `full` and
`max_age_hours` to zero; these can be overridden by configuration. Read saved
public request logs for the actual call. Adding a report does not require editing
these defaults. Runtime parameter rules can fill request fields, replace or append
parameter descriptions, and validate allowed values, patterns and finite numbers.

The `kind` field is a report grouping (native browser or adapter), not a complete
product taxonomy. It does not select transport or credentials. Keep these concerns
in their existing execution and access owners rather than duplicating them.

An adapter may declare `task_mode_provider_type` when its planned task modes
use an existing execution selector contract under a different transport name.
For example, the Moli WebFetch transport uses the native Markdown task-mode
contract. This mapping does not override recorded output routes or waits.


### Native resource projection versions

New `html_attribute_urls` declarations record `srcset_parser: html-candidates-v1`.
This keeps commas inside URLs, recognizes candidate descriptors and preserves
`data:` URLs. Changing these semantics requires a new parser identity; keep
previous identities reproducible. New calls and offline correction requests
must declare their parser instead of inheriting whichever dependency is installed.

Historical declarations without a parser can replay `srcset` only when their
bound scoring-source identity is registered in
`src/harness/web_fetch/data/native-projection-history.json`. Those historical scorers
included the projection implementation in their source hash. The registry maps
each proven source hash to its parser, including versions
that already used the current parser but omitted its declaration. It names
the exact Git revision, projection source path and source SHA-256; it contains
no task IDs, measured outputs or result-dependent exceptions. The retained
comma-splitting behavior is for verifying historical receipts, not new calls.
Unknown identities and conflicting explicit parser declarations fail closed.
URL attributes other than `srcset` do not depend on this parser selection.

Revision-chain validation uses the correction receipt's scoring-source identity,
not a later rescore's identity. Saved-answer validation uses the record's bound
scoring identity. Both compare the complete reconstructed answer with the saved
answer; neither tries another parser after a mismatch. No historical results,
raw responses, scores or receipts are rewritten by this compatibility reader.

Before registering another historical identity, recompute its scoring-source
hash from the exact ordered source list in that revision's `scoring.py` and
verify the projection source hash. A matching saved output alone is insufficient.
The source-bound replay and rejection controls are in
`tests/test_native_answer_projection.py`.
