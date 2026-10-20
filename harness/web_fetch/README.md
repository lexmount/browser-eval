# Web Fetch

Browser Eval provides this track’s execution, evidence and report pipeline. The self-authored task suites and acceptance requirements are maintained in [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch). See [benchmark integration](../../docs/benchmarks.md) for verified input export and repository boundaries.

Fetch a page or file, score the returned content against declared requirements, and inspect the saved result. Start with the [local article example](../../examples/web_fetch/README.md) for a runnable HTTP/Moli/Chromium comparison.

This page is the technical reference for larger experiments and historical reports. Use the section matching your task:

| I want to… | Read |
| --- | --- |
| Run local browsers or reproduce the live-URL comparison | [Browser comparison guide](HEADLESS.md) |
| Compare extraction from frozen HTML | [WebMainBench guide](WEBMAINBENCH.md) |
| Understand task pass rules and text quality | [Text evaluation](#common-text-evaluation) · [stable acceptance](#stable-acceptance-and-text-quality) |
| Capture an independent reference | [Source capture](#independent-source-capture) · [browser observations](#independent-browser-observations) |
| Define dynamic-page completion | [Dynamic-content protocol](#dynamic-content-ground-truth-protocol) |
| Check timeouts, retries or revise scores | [Budgets and measurements](#end-to-end-budget-and-missing-measurements) |
| Build a new report instance | [Report instances](#report-instances) |
| Score and publish WCXB | [WCXB scoring](#wcxb-scoring) · [timing and publication](#wcxb-paired-timing-and-publication) |


## Report revisions and file-delivery controls

Measured outputs are persistent local data, not source files. The current evidence
bundle lives at `~/.browser-eval/evidence/current/` by default; set
`WEB_FETCH_EVIDENCE_DIR` to use another explicit bundle. Raw research reruns live
under `~/.browser-eval/evidence/research/`; use
`WEB_FETCH_RESEARCH_EVIDENCE_DIR` when they are stored elsewhere. Git retains compact
task catalogs, acceptance contracts, annotations and input hash manifests. Full
historical datasets and source captures are installed outside the checkout; see
[frozen reference inputs](../../docs/reference-inputs.md). Git ignores provider artifacts,
results, execution manifests, selection receipts and internal audit notes.

`research/report-changes.json` drives the public, collapsible `changes/index.html`.
Record each substantive release's issue, change, score effect, evidence and limits.
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
`scripts/capture_headless_browser_observations.py` command only parses the shared
entry point. Install the `headless-eval` extra and a Playwright Chromium
browser, or pass `--browser-binary` with an installed Chromium executable.
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

## WCXB scoring

`wcxb_scoring.py` owns certified frozen-HTML scoring. The maintained command is
`scripts/score_wcxb_results.py`; `--profile` selects an explicit experiment profile
and defaults to `evaluation/wcxb/current-report.json`. The profile's ordered
participants must match the cohort certificate exactly. Adding or renaming a
participant in a separately certified cohort requires data changes rather than
scorer branches. This scoring option does not change the current report's
publication authority or the certification runner's supported participants.

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

Text acceptance reads the visible prose of raw HTML embedded in Markdown. HTML
attributes, scripts and explicitly hidden elements cannot supply body coverage
or required facts. Literal HTML in code examples remains answer text. This
projection runs inside the shared scorer and leaves provider artifacts unchanged.
`required_patterns` defaults to `match_scope: "text"`; a rule that checks serialized
output structure, such as an HTML table tag, must declare `match_scope: "output"`.
Unknown scopes are rejected. Use paired controls in `evaluation/scoring-regressions.json`
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

Live WebFetch reports are data instances, not renderer subclasses. An instance's
`evaluation/<instance>/current-report.json` binds its complete `cohort_plan`,
implementation, external `diagnostic_input` (data-home-relative path and SHA-256),
and publication target. The input manifest owns cohort evidence paths, reviewed
findings, explanatory prose and examples. It does not contain rendering code.

```bash
uv run --frozen python scripts/build_report.py --profile evaluation/<instance>/current-report.json --output <new-report-directory>
uv run --frozen python scripts/check_diagnostic_report.py <new-report-directory> --profile evaluation/<instance>/current-report.json
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
After export, `check_diagnostic_report.py --deployed-archive <export.zip>` compares
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

All methods default to one retry after a failed task. Set
`--max-failure-retries N` to any nonnegative integer; `0` disables retries.
There are at most N+1 calls per task, stopping as soon as the task passes.
The same rule covers request errors, timeouts, empty output and content-rule
failures, including methods using native parameters.

Each method and task produces one result. The final call replaces the earlier
answer, verdict, metrics and primary latency; retries do not add samples or a
separate result category. Call artifacts remain available for audit, with
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

`scripts/rescore_content.py` recomputes
all applicable records through the shared scorer without supplier calls.
Known scoring failure modes have compact synthetic positive and negative controls
in `evaluation/scoring-regressions.json`; CI executes every control through the
same `score_attempt` orchestration and task predicate used by live and offline
scoring. A new decision-changing scoring correction, or a frozen-reference
correction via `scripts/revise_reference_evidence.py`, must use its revision
command with a concrete `--root-cause-category`,
`--root-cause`, and one or more `--regression-id` values. The command validates
those controls before touching evidence and writes their IDs plus FP/FN direction
counts into the revision receipt. Directly editing stored verdicts is not a
supported correction path.

Reference corrections use `harness.web_fetch.reference_revision`; the
`revise_reference_evidence.py` command invokes that same owner. It validates
existing revision histories before any change, stages the full evidence bundle,
and copies only the selected cases' corrected reference inputs. A failed copy,
score or receipt write leaves the original bundle intact. Success retains the
predecessor beside the evidence directory under `.NAME-reference-history-*/original`;
these private copies preserve prior references and are excluded from the report.

Offline rescoring and scoring revisions are available through
`uv run --frozen python -m harness.web_fetch.rescoring --help` and
`uv run --frozen python -m harness.web_fetch.scoring_revision --help`. The existing
`rescore_content.py` and `revise_body_scoring.py` commands use the same owners.
Supply an evidence directory and, for rule revisions, an explicit canonical
input plus the required root cause and executable regression IDs. Neither
operation calls providers. Declared evidence must remain present, unmodified
and free of symbolic links before scores can be rewritten.

### Retain or merge an existing review

Existing independent judgments can be retained after a score-only correction
with `scripts/rebase_blind_result_review.py`. It requires identical task and
reviewer-visible inputs, retains the independent verdicts, and requires explicit
adjudication for remaining disagreements. `--output` stages a validated blind
review; `--install` also requires the complete publication review checks.
The output cannot overwrite input evidence or the prior review.

`scripts/merge_result_review_revision.py` combines a prior complete review with
already recorded supplemental judgments. Use `--prior-review` for a bound scoring
revision chain, or `--prior-evidence` for a refreshed result matrix. Changed tasks
invalidate reviews across all methods; rerun outputs invalidate the affected
attempts. Both commands validate before installation and leave the installed
review unchanged on validation or write failure. They do not initiate a review;
independent review still requires an explicit request.
A merged blind review still needs any anomaly investigation required by the
dataset before publication.

### Request an independent review

Independent execution remains opt-in through `scripts/review_headless_results.py`;
its default scope is machine failures. Reviewing passes or the full matrix also
requires `--confirm-full-review`. `--prepare-only` creates blinded packets without
starting a reviewer. `scripts/review_result_anomalies.py` investigates an already
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
`harness/trajectory_replay`; autonomous browser-use evaluation lives in
`harness/browser_use`.

From the repository root:

The fixture commands need no historical inputs. The Moli command below uses the
frozen historical dataset; first [import its approved input copy](../../docs/reference-inputs.md).
Its `evaluation/dataset.json` name resolves through the input manifest, while the
public task directory contains only metadata and definition hashes.

```sh
make setup
uv run --frozen python -m harness.web_fetch validate --providers fixture
uv run --frozen python -m harness.web_fetch run --providers fixture
uv run --frozen python -m harness.web_fetch run --dataset evaluation/dataset.json --provider-config configs/providers.example.json --providers moli-webfetch --case-ids static-simple
make report EVIDENCE=/absolute/path/to/evidence
```

Credentials and local executable/repository paths belong in the root `.env`
(copy `.env.example` and fill only the methods you use). Exported environment
variables take precedence; `--env-file` overrides which file is read.

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

Merge and append measured dynamic supplements through the same shared module.
Pass private input directories explicitly; merged output must be new.
These commands make no provider calls and do not rescore:

```sh
uv run --frozen python scripts/merge_dynamic_supplement.py \
  --reference /absolute/path/frozen-reference \
  --runs /absolute/path/run-a /absolute/path/run-b \
  --output /absolute/path/new-assembled-supplement
uv run --frozen python scripts/append_dynamic_content.py \
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
   provider outputs. Freeze required fact groups before scoring; every group
   needs at least one nonempty alternative and must occur in both reference and
   rendered text. Include tail/item-count checks to catch partial lists.

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
regardless of `fact_coverage_threshold`. Empty facts are invalid, not 100%.

Classifications describe the required facts, not the entire page:

- `initial_visible_content`: all groups found in a static DOM text proxy.
- `embedded_payload`: all groups recoverable across initial DOM/source literals,
  but not all in static DOM text. This rules out a “must execute JS” claim based
  on these facts alone. It includes hidden/template/metadata content.
- `partial_initial_content`: only some groups found in the initial response.
- `absent_from_initial_evidence`: no groups found by this mechanical audit.
- `unclassified`: no required facts were supplied; validation fails.

The static visibility proxy excludes scripts, head, templates, noscript and
explicitly hidden elements; it cannot compute stylesheets, layout or browser
HTML repair. Payload detection recognizes source literals and JSON string
escapes, not arbitrary expressions, compression or external scripts. Therefore
absence is not proof of JS necessity: a follow-up HTTP/API request may suffice.
`js_required_proven` is always false. Likewise, `valid` checks declared
completion and fact/length gates; it does not independently verify the declared
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
   `scripts/finalize_reference_checks.py --run-dir RUN --observations FILE`.
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
outside Git with `uv run --frozen python scripts/build_native_dns.py
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

A public-DNS configuration must bind `harness/web_fetch/workers/public_dns.mjs` for the Node
workers, `harness/web_fetch/network_dns.py` for CowAgent/LangChain/Crawl4AI, and DeepSeek's
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
`harness/web_fetch/workers/dsh_output.mjs` is required for this projection. Old calls without the
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

The WCXB command defaults to English, with a matching Chinese edition. Use
`--language zh` for Chinese-only output; its English entry shows a not-generated
notice. Bilingual release checks require matching routes and values. English
repair content must be present in the bound asset set for an English build.
See [report languages](../../docs/report-hosting.md#report-languages) for all builders.

`wcxb_latency.py` owns paired-call collection, receipt validation and certification.
The collector uses the profile's complete qualified population, timeout and binary
hashes. It runs two serial rounds, reverses order per task and selects the final
round before observing results. Failed calls remain in the physical log; an
incomplete, drifting or invalid cohort receives no valid receipt.

```bash
uv run --frozen python -m harness.web_fetch.wcxb_latency \
  --profile /absolute/path/current-report.json \
  --dataset /absolute/path/wcxb --manifest /absolute/path/qualified.json \
  --release /absolute/path/released-binary --candidate /absolute/path/candidate-binary \
  --output /absolute/path/new-paired-evidence
uv run --frozen python -m harness.web_fetch.wcxb_latency certify \
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
uv run --frozen python -m harness.web_fetch.wcxb_publication \
  --authority-root /absolute/path/browser-eval --source /absolute/path/wcxb-report
```

The publisher freezes the package and profile, checks the expected remote version,
verifies exported file bytes and the public share page, then advances only the
profile's live publication version. A failed export check retains the uploaded
version in a private operation journal so verification can resume without
uploading the package again. Publication work files use the configured data home.

## Reference candidates

`reference_freeze.py` owns candidate preparation and reviewed promotion as well
as reference freezing. The command scripts delegate to that owner. Installed
callers can inspect both interfaces without a repository checkout:

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

`review_webmainbench_results.py` is an explicit deterministic audit entry. It
checks saved evidence without calling a provider or importing the official
scorer for its independent text, code and formula checks. Its receipt binds the
profile and every reviewed record, output, summary, execution record and fixture
certificate. Applicable metric failures count as zero; a metric explicitly marked
as reference-inapplicable is excluded. This audit does not substitute for a human
result review.

After an explicitly requested audit has produced a passing receipt, assemble it:

```bash
uv run --frozen python scripts/assemble_webmainbench_evidence.py \
  --source-root /absolute/path/provider-shards \
  --dataset /absolute/path/WebMainBench_545.jsonl \
  --sample-manifest evaluation/public-webmainbench-200.json \
  --profile evaluation/current-report.json \
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

`local_revision.revise_local_snapshot` only rescores a separate saved snapshot.
Changed contracts require `rule_revision: true`. Its receipt binds the predecessor,
selected complete method/task scope, regression IDs, source hashes, explicit contract
changes, actual scoring time and verdict changes. Physical output bytes, call identities,
timestamps and latencies remain unchanged. Proven environment defects exclude a
case symmetrically across every method. The result ledger derives all transitions
from per-attempt states; overridden review opinions require task/answer hashes,
original answer excerpts, concrete rules and a reason. This is supplemental
adjudication, not a new blind review of the entire matrix.

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
按任务类型预选官方支持的正文、资源或原始文件接口。JSON 包装中的原生 Markdown
可以解包；不能把 JSON DOM、HTML 或下载的 PDF/DOCX 另行转换成选手的正文成绩。
Chromium 没有原生 Markdown，网页 HTML 使用固定版本的 node-html-markdown；此例外
不授权外部文档解析器。浏览器内已经加载的 PDF 查看器可通过原生脚本接口读取文字，
不得下载嵌入文件后交给外部解析器。图片、视频的原始文件交付只验收字节，不冒充正文提取。

Lightpanda 使用 `--dump markdown --json` 的原生正文；Obscura 使用其固定官方
`HTML_TO_MARKDOWN_JS`，脚本哈希必须与所测版本绑定；Kitesurf 使用显式指定该浏览器
的官方 `/markdown` 或 CDP 接口。Moli 的网页正文、文档正文、结构与网页内嵌媒体
统一使用原生 Markdown；网页媒体只核对同一次输出里的目标地址。直接媒体文件才走
原始响应。禁止失败后切换为 HTML、JSON、网络跟踪或查看器脚本补结果。

`output_mode_for_case` 负责路线选择，`browser_task_interface_matrix` 使用同一选择器
生成每类任务与每种浏览器的接口表。`require_native_task_routes` 检查实测路线与外部
转换标记；当前报告配置必须启用该门禁，检查失败时不得汇总或发布。
改变调用接口应冻结完整受影响范围、保留旧调用并通过认证入口补跑；只改共享评分时
离线重算所有选手，不重复请求网站。合成正反测试必须同时覆盖正确正文缺少网址和错误正文。

方法名与浏览器引擎分开登记在 `configs/providers.headless.json`。同一浏览器的发布版和分支构建分别计分，
原生路线检查、接口表和官方能力说明共用该登记的引擎；未知方法不能跳过检查。
报告分别统计浏览器引擎和执行配置，同一引擎的多个版本不算成多种浏览器。
资源属性中的单个 URL 保留完整地址；只有 `srcset` 按候选及描述符解析，URL 中间的逗号不能拆开。
分支构建在调用前固定来源提交、基线提交、构建环境和二进制 SHA-256。新增配置只调用完整固定题库，
旧配置的原始结果保持不变。不同日期的批次用于展示各自任务完成情况；代码带来的耗时变化须另按同窗配对协议测量。

### 资源比较的统计范围

`resource_cohort.py` 的主统计保留预先确定的完整方法与任务矩阵，包括失败调用。
平均值、P50 和 P95 使用同一范围，报告同时显示各方法的通过数。各方法成功调用的统计另存为
条件分布；所有方法共同通过的任务只作附加诊断，不能据此挑选排名样本。没有共同成功项时，
仍须保留完整调用的资源统计；没有成功项的条件指标为不可用，不填零。

改正统计范围时，用已保存、哈希校验通过的测量重新汇总，不重新调用浏览器，不覆盖旧汇总。
主结果、原始输出和每次调用的资源计数保持不变。发布检查核对全部测量值、分母、指标方向
及唯一最低值标记；并列项保持中性。资源消耗与任务通过率分别呈现，不合成总分。

### 已编译评测集的重跑

`scripts/run_certified_benchmark.py` 直接使用冻结的题目与验收规则。已有固定答案或稳定规则的题目
不重新审核入选资格，不读取历史资格调查原文，也不因旧归档缺失而改成诊断结果。
新增题目、修改参考或验收规则时，仍须通过相应构建与修订流程核验来源。
固定答案不会因为有来源核验信息、旧核验日期或缺少新版验收契约而自动变成实时参考。
只有题目明确声明 `source_verification.reference_mode: live_exact`，才执行调用前冻结和调用后核验。
没有该声明时，执行器保持题库中的答案和评分规则不变。明确修订参考答案仍走独立的参考修订入口。

已授权的错误修正应自动完成：先查明共享原因并添加必要的正反测试，再冻结受影响的完整调用范围，经认证入口补跑。
接口、版本或执行错误需要新调用；评分或展示错误只重算或重建。不能为提高分数挑选失败题重试，
不能让无关题目重复调用，也不能把已有题库的重跑变成重新收集资格证明。保留旧结果及修订影响。
评测配置错误或适配器在调用前拦截属于执行问题，不能当作浏览器能力失败发布；原生路线门禁拒绝 `configuration` 结果。供应商实际调用返回的错误仍按原规则保留。


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
`scripts/package_artifact_hub.py` and `scripts/check_publication_bundle.py` when
sharing a previously validated frozen local report. Both commands verify its
saved matrix, scorer identity, evidence and revision bindings through the existing
presentation validator. This does not certify the cohort or add result reviews;
its visible evidence-state and review-scope disclosures remain intact. Without
this explicit binding, publication retains the certified-evidence gate.

The profile-bound publisher accepts the same binding. Use `--visibility unlisted`
for ordinary link sharing at the existing `/s/<slug>` address; `private` and
`public` must follow the requested access scope. The publisher verifies the
actual access state without creating an independent share link.

Large publication trees automatically use content-defined deduplication and a
compressed catalog when fixed-block storage exceeds the upload budget. Both
encodings retain complete virtual files, source bindings and file hashes.
An acceptance contract stays inline when no standalone contract file exists.
To reuse a completed public content cache, pass `--cached-content <directory>`
to `scripts/package_artifact_hub.py` with the same source and, when applicable,
`--presentation-binding`. The packer refreshes source-bound result privacy and
footprint projections when the cache uses an earlier representation; it rejects
a cache bound to another source. `--refresh-privacy` additionally applies the
current privacy checks to other cached files. Complete publication checks run
before the archive is installed. Source and input cache remain unchanged.
Saved public snapshots may retain original scored hashes while storing separately
bound public evidence views. Verify both identities through the existing public-view
helpers; report retained views separately instead of calling them original bytes.

If the complete compressed report still exceeds a deployment's artifact capacity,
use `--parts-directory <new-directory>` to prepare a main archive and capacity-bound
data attachments. The preparation gate checks their combined evidence, links and
hashes. Publish the attachments, bind each exact committed version with
`bind_data_parts`, and pass `--parts-manifest <directory>/parts.json` when publishing
the main report. Ordinary publication rejects unresolved or mismatched attachments.
Keep the main profile's `data_publications` registry aligned with that bound plan;
main and data artifacts must use the same profile-bound service.
