# Five-browser HTML → Markdown comparison

This code PR does not include the newly captured raw evidence. All 750 selected
calls and rejected-cohort diagnostics remain on the execution machine. Raw
public-page HTML contains Google API-key-shaped strings, so the full evidence
push was blocked by the safety check. Only code, tests and a scoped diagnostic
summary are included. Import verified evidence separately before rebuilding the
new report; without `evaluation/headless/`, the builder uses the original cohort.

The cohort uses the existing 150 task IDs: web 100, document/image/video/
challenge/dynamic 10 each. The implementation stays in the shared Web Fetch
harness and uses `score_attempt`; no engine-specific scoring or task answers are
passed to a browser. The supplied `webfetch-headless-browsers` wrapper motivated
the CLI HTML → pyhtml2md contract. The repository adapter adds Chromium and
Kitesurf CDP, bounded processes, raw HTML evidence, hashes and lifecycle checks.

## Runtime used

| Engine | Runtime | Interface |
|---|---|---|
| Moli | 1.1.1, macOS arm64 | `fetch --dump html` |
| Lightpanda | 1.0.0-nightly.9142+cb26e0553, macOS arm64 | `fetch --dump html` |
| Obscura | 0.2.1, macOS arm64 | standard `fetch --dump html`, no stealth |
| Chromium | Chrome for Testing 145.0.7632.6, Playwright 1.58.0 | local CDP |
| Kitesurf | public beta endpoint; version where returned by CDP | remote CDP |

All use `domcontentloaded`, then whole DOM HTML and pyhtml2md 1.7.0. CDP uses
`document.documentElement.outerHTML` because the public Kitesurf endpoint did
not implement the XMLSerializer used by Playwright `page.content()` during the
interface probe. Both CDP engines use the same expression. No click, scroll,
business-specific selector, output-format fallback or separate file parser is
enabled. CLI timeout units are translated explicitly (Obscura seconds, the
other two milliseconds). The outer 45-second budget includes conversion.

Kitesurf is remote while four engines run locally. With up to five concurrent
providers, local CPU contention and remote/network conditions are not isolated.
Each provider visits tasks sequentially, so calls are not simultaneous paired
experiments. This measures cold-call client latency and task delivery, not
CPU/RSS, sustained throughput, engine-only speed or billing. One call per pair
does not establish repeatability or confidence intervals.

## Run and preserve evidence

Install the `headless-eval` optional dependency group in a repository-local
environment. Obtain the four binaries from their official releases. Set
`MOLI_BINARY`, `LIGHTPANDA_BINARY`, `OBSCURA_BINARY`, `CHROMIUM_BINARY` in the
repository `.env` or exported environment. `KITESURF_CDP_ENDPOINT` defaults to
`wss://kitesurf.cloudflare.app/devtools/browser`; no token was used. Binaries are
not committed. Their measured SHA-256 values are in every local call record.

The reference lifecycle in [README.md](README.md) remains mandatory. Independently
capture ordinary pages using `scripts/collect_source_evidence.py`; separately
observe browser-dependent references over their complete declared scope. Do not
copy a candidate output into a reference or reuse an old observation timestamp.
Freeze using:

```sh
python scripts/freeze_headless_references.py --captures CAPTURES --output FROZEN --browser-observations BROWSER_PRECHECK.json
python scripts/run_headless_benchmark.py --dataset FROZEN/dataset.json --output RUN --workers 5
```

For a short subset, use `--case-ids id1,id2` or `--tracks dynamic`. A failed or
incomplete measurement directory must not be overwritten. Select short batches
when the call duration would exceed reference freshness. While the run is
active, collect and certify independent observations promptly:

```sh
python scripts/postcheck_headless.py --run-dir RUN --captures POSTCHECK --browser-observations BROWSER_POSTCHECK.json
python scripts/certify_headless_cases.py --run-dir RUN --observations POSTCHECK/observations.json --output CERTIFICATES
```

Browser observation JSON maps case ID to `independent`, `checked_at`,
`content_text`, `selector` and bounded `completion` (`satisfied`, `elapsed_ms`,
`timeout_ms`). Postchecks require capture after the last relevant call. The
certifier partitions an already preflighted live run by completed matched case,
retains the original binding, source manifest bytes and exact attempt records,
and applies the existing verifier at actual certification time. It cannot
certify a legacy run, overwrite a failed certificate or backdate a check.

The complete original run remains diagnostic evidence. A failed reference
postcheck suspends that case's new body measurement, not the browser's task.
Preserve it and run a fresh independently bound cohort; never select repeated
provider attempts by score. Stable dynamic/popular contracts are not subject to
the exact-snapshot expiry gate.

## Evidence and report

Each successful HTML response retains both HTML and converted Markdown. CLI
non-UTF-8 stdout is retained separately as binary evidence; this does not claim
that file-task acceptance passed. Unavailable HTTP status/final URL stay null.
Timeouts retain a call result even if the worker never returned phase timings.
`fetch_html_ms` includes the browser lifecycle and transport; `conversion_ms`
is separate. End-to-end latency excludes pre-call throttle and post-call scoring.

Use `evaluation/headless/` for the complete verified cohort, preserving the
original `evaluation/` evidence. `scripts/build_report.py` selects this cohort
when present and builds the one current report at `reports/webfetch/`.
Unverified lifecycle records cannot pass the report builder. The current
renderer serves both the evidence browser and one self-contained authored HTML
article. Existing registered interface examples and adoption statistics stay
available, explicitly separate from this cohort's measured providers.

```sh
python -m unittest discover -s tests
node --test tests/test_report_ui.mjs tests/test_report_loader.mjs
python scripts/build_report.py
python scripts/check_report.py reports/webfetch
python scripts/check_report_links.py reports/webfetch
python scripts/check_report_revision.py reports/webfetch
```

Publish with the existing Artifact Hub packager/publisher. Keep the current
site/share identity, acquire owner authorization if unavailable, and verify
remote bytes. Publication tokens belong only in the ignored repository `.env`.

## Measurement correction before the final cohort

An initial diagnostic run exposed quadratic whitespace backtracking in
`normalize_for_match`. Large DOM-derived text held Python's GIL and delayed
other providers' deadline checks. Those runs were stopped and retained locally
as invalid timing diagnostics, not selected as benchmark results. The regex now
starts its whitespace lookahead only at run boundaries. Exhaustive short-string
equivalence and long-whitespace regression tests cover the semantics; the
scoring predicate and task rules did not change. The final cohort began after
this correction and retains its scoring-source hash.
