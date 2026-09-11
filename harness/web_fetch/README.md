# Web Fetch Harness

## Report revisions and file-delivery controls

Measured outputs are persistent local data, not source files. The current evidence
bundle lives at `~/.browser-eval/evidence/current/` by default; set
`WEB_FETCH_EVIDENCE_DIR` to use another explicit bundle. Raw research reruns live
under `~/.browser-eval/evidence/research/`; use
`WEB_FETCH_RESEARCH_EVIDENCE_DIR` when they are stored elsewhere. Git retains the frozen dataset,
annotations and independent source evidence, but ignores provider artifacts,
results, execution manifests, selection receipts and internal audit notes.

`research/report-changes.json` drives the public, collapsible `changes/index.html`.
Record each substantive release's issue, change, score effect, evidence and limits.
Keep the measured dataset/results untouched for presentation-only revisions.

The ranking column `判分完整性` is judged calls divided by planned calls, not a
success rate or text-completeness score. Every planned provider/case pair must
exist exactly once and be judged. Any missing, duplicate or unjudged pair marks
the report unreliable; validation and packaging reject it. Missing historical
file bytes cannot be reconstructed from a URL. An independent HTTP control can
exercise the byte pipeline without pretending to be a provider result:

```bash
uv run --frozen python -B scripts/check_file_delivery.py --output ~/.browser-eval/evidence/research/file-delivery-check
```

This writes three fresh call records, returned binaries and their target-hash
checks, and a supplier configuration preflight. It does not overwrite historical
records or fill any provider observation. Provider reruns need their
runtime/credentials and a supported byte-returning adapter; text/links alone are
not a file deliverable. Persist the control under
`~/.browser-eval/evidence/research/file-delivery-check/`.

## Common text evaluation

Website, dynamic-site, popular-site and document text share `scoring.score_attempt`:
request eligibility, error-page checks, declared text/record requirements, resource
checks where applicable, and one authoritative task predicate. Live execution and
offline rescoring use the same entry point. Case contracts define the differences;
provider identity never selects a more lenient evaluator.

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

## End-to-end budget and missing measurements

The common call boundary includes provider acquisition, network retries, rendering,
local extraction and required answer normalization. Artifact persistence, scoring,
reporting and between-call throttling are outside this latency. Runtime source and
configuration hashes are captured before calls. Moli binary hashing occurs during
validation, not each measured fetch; a changed binary requires revalidation.

Runs default to no benchmark-level retry. A run may predeclare one failure retry
with `max_failure_retries=1` (the headless CLI flag is
`--max-failure-retries 1`). The retry is triggered only after the first physical
call fails the shared task predicate. Both physical outputs and supplemental
artifacts are retained; the logical record always uses the final physical call,
never whichever response scored better. Its displayed latency sums both physical
call intervals and any provider-required wait between them. A correction for a
proven wrong command, version, scorer or reference is not a retry: supersede the
affected result explicitly and rerun the complete declared correction scope.

Late answers cannot pass. A common daemon boundary stops accepting output at the
deadline and requests cancellation. An adapter that cannot cancel is quarantined:
remaining calls on it are recorded as unexecuted, not fabricated failures, and
the run exits with an incomplete-evaluation error. This avoids overlapping
abandoned requests while preventing a partial run from being treated as a
finished benchmark. Native subprocess adapters cancel their processes; cleanup
is never extra answer budget.

New file evaluations accept `ProviderResult.files` entries containing actual
`data: bytes`, retain separate binary artifacts and verify signature, length,
SHA-256 and a declared independent target hash when present. Link-only output
cannot pass a measured byte contract. This does not decode or understand media.
Historical records without `raw_file_pass` are unmeasured even if their HTTP
request failed: all report/summary denominators and failure filters exclude them.
The three result filters remain pass/fail/all; unmeasured rows appear in all.

`scripts/correct_evaluation_contracts.py` applies reviewed scope and contract
changes without reading provider outputs. `scripts/rescore_content.py` rescales
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
`scripts/revise_fairness.py --baseline-ref <original-commit>` also records original
and revised scores in the persistent bundle's `scoring-revisions.json`, checking original
output hashes, identities and timing. Historical hard deadlines and timing
overhead cannot be retroactively certified. For a heterogeneous same-window
cohort, keep every predeclared call and report all-call latency (one shared task
denominator) separately from successful-call latency (each method's conditional
subset). The all-method common-pass intersection is diagnostic only: never
reselect tasks after seeing outcomes.

`content_check_coverage` measures matched required text groups, not body recall.
All declared groups are required by stable contracts; record count/type/uniqueness
remain independent requirements. Empty text groups yield null coverage. The report
shows the number of covered cases and does not equate unequal check difficulty.

Only complete comparable references support body precision, recall and F1. Partial
headings/labels cannot support precision over a whole output: additional legitimate
text is unjudged, not noise. Snapshot scores containing explicitly nonliteral fields
are retained as attempt diagnostics but excluded from invariant body aggregates.
Returned-title quality is also auxiliary and never substitutes for body scores.

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
live in this package. Browser Use remains independent in `harness/browser_use`.

From the repository root:

```sh
make setup
uv run --frozen python -m harness.web_fetch validate --providers fixture
uv run --frozen python -m harness.web_fetch run --dataset configs/fixture-dataset.json --providers fixture
uv run --frozen python -m harness.web_fetch run --providers moli-webfetch --case-ids static-simple
make report EVIDENCE=/absolute/path/to/evidence
```

Credentials and local executable/repository paths belong in the root `.env`
(copy `.env.example` and fill only the methods you use). Exported environment
variables take precedence; `--env-file` overrides which file is read.

`moli-webfetch` invokes the official local Moli CLI; set `MOLI_BINARY` when it
is not on PATH. Body tasks use `moli fetch --dump markdown`. Direct file tasks
use bare `moli fetch URL`, without `--dump` or page-wait flags, so the evaluator
can receive and verify original bytes. No alternate provider or output-format
fallback is used after a failed call. The seven-method cohort's direct PDF and
DOCX body route is defined separately in [HEADLESS.md](HEADLESS.md).

The installed console command is `browser-harness-eval`. There is no legacy
Python package or compatibility entrypoint.

## Dynamic-content ground-truth protocol

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
`track: dynamic` identifies 动态网站正文. The existing
`scripts/run_dynamic_tasks.py` performs popular-site list-identity checks,
not body evaluation. Never repurpose those calls as body measurements.
Local fixture URLs are unavailable to cloud providers and are blocked by some
private-network guards; compare them locally or deliberately host a public
fixture. Avoid the answer-replaying `fixture` provider as a rendering baseline.

## Stable acceptance and text quality

Rendering requirements and value stability are separate axes. Most of the
dynamic examples are fixed demonstration data delivered after loading; popular
rankings and catalogs change frequently. Annotate fields, not whole websites:

- Stable document titles, headings and instructions can be explicit
  `stable_text_anchors` with independently justified equivalent alternatives.
- Frequently changing names, prices, dates and ranking positions use an
  `acceptance_contract`: required fields, types, record grouping, minimum count,
  uniqueness and optional stable page identity. Values are not compared to a
  previous ranking. Duplicate cards cannot satisfy the minimum.
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
