# Web Fetch Harness

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
python -m harness.web_fetch validate --providers fixture
python -m harness.web_fetch run --dataset configs/fixture-dataset.json --providers fixture
python -m harness.web_fetch run --providers moli-webfetch --case-ids static-simple
python scripts/build_report.py
```

Credentials and local executable/repository paths belong in the root `.env`
(copy `.env.example` and fill only the methods you use). Exported environment
variables take precedence; `--env-file` overrides which file is read.

`moli-webfetch` invokes the official local `moli fetch --dump markdown` CLI;
set `MOLI_BINARY` when it is not on PATH. It needs no API key. No alternate
provider or output-format fallback is used after a failed call. Screenshots
and PDF export are official Moli capabilities, not enabled by this text adapter.

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
python -B -m unittest discover -s tests -p 'test_render_evidence.py'
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
