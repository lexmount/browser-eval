# Web Fetch Harness

Web Fetch providers, execution, reference checks, scoring and report generation
live in this package. Browser Use remains independent in `harness/browser_use`.

From the repository root:

```sh
python -m harness.web_fetch validate --providers fixture
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
Use a separate dataset and output directory for the new cohort; the existing
`scripts/run_dynamic_tasks.py` means challenge-list reruns and overwrites
canonical evidence. A new `track` value also needs explicit report support
(`TRACKS`/contracts), otherwise use a separate dataset with an existing `web`
track until that support is added. Do not mix scores into the existing cohort.
Local fixture URLs are unavailable to cloud providers and are blocked by some
private-network guards; compare them locally or deliberately host a public
fixture. Avoid the answer-replaying `fixture` provider as a rendering baseline.
