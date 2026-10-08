# Report and release

## Template discovery and diagnostic models

Run `make report-template TRACK=webfetch` (or `trajectory-replay` / `browser-use`) before authoring. The registry returns the maintained owner, current profile, version-pinned example, build and check commands. A reference report supplies presentation conventions, never evidence or the new publication target. Extend the owner instead of copying its HTML into a temporary renderer.

For explicitly requested live WebFetch diagnostics, use `make webfetch-diagnostic-report EVIDENCE=/absolute/source OUTPUT=/absolute/new-output`. The `webfetch_cohort_diagnostic/1` model contains cohorts, per-case Boolean first-call verdicts, declared summary counts, cause/source labels, review counts and hash-bound reference/output/source/log files. `tests/test_report_templates.py:diagnostic_example` is a runnable two-scenario fixture through the public builder; its test command is registered alongside the variant. The release checker recomputes totals and validates rendered verdicts, complete evidence bindings, dialogs and links. A diagnostic remains diagnostic; this path cannot substitute for a certified build.

For a new report type, first persist a reusable example and scenario-based positive/negative tests, register its owner and release gate, then generate the requested report. Later corrections update the owner and relevant rules when supported by evidence; do not clone a template per run or embed report-specific conclusions in instructions.

Diagnostic failure analysis derives unmet requirements and expected/actual values from the saved score-bearing call, separately from independent source observations and supplemental calls. Count each affected task once per failure type, disclose overlaps, and link counts to the matching task inventory. Source access failure and later success are observations, not engine root causes. Release checks recompute the type counts and detail explanations from bound evidence and reject contradictory or missing values.

## Language generation

All tracks generate and validate Chinese by default. English requires an explicit manual trigger; do not automatically translate prose, generate English illustrations, render English routes or run English-specific checks. A manifest records which languages were generated. When English is absent, retain only a clear not-generated notice with a return link, and test that control. Remove stale English output and unused translated assets from the new bundle while preserving source translations. Parity and English localization checks apply when the English edition is explicitly generated; Chinese-only releases retain all Chinese evidence, semantics, links and visual gates.

For the current trajectory report, `make trajectory-replay-report EVIDENCE=/absolute/path/results.json` builds Chinese only. Explicit `ENGLISH=1` (or the build script's `--include-english`) enables English. Report, visual and packaging checks use the generated manifest, so they do not regenerate an absent edition.

## Reader-facing structure

Make the home and interpretation routes presentation-ready. Each section should make one finding legible through a data-bearing chart, mechanism diagram, meaningful image, or inspectable specimen, followed by short explanatory prose. Keep exact values in accessible text. Decoration is not a visual explanation: remove images that carry no data, mechanism, topology, or evidence. Dense sortable inventories and raw logs remain on the analysis and evidence routes that own them.

When multiple methods have material failures, give each one a separate interpretation chapter containing its full reviewed subtype distribution, a plain-language explanation of the dominant mechanisms, representative result evidence, and the difference from the other methods. The home page should show only a compact leading-error signature; result analysis owns complete stage and case distributions.

After the factual pass, run both repository Humanizer and Humanizer Chinese over Chinese report-authored headings, prose, and captions. Run Humanizer over English. These passes may improve language only; they must preserve numbers, scope, technical meaning, quotations, and uncertainty.

Organize around reader questions, not storage tables. A useful report usually makes these answers easy to find:

- What was compared, on which tasks and versions?
- What is the headline result, with denominator and uncertainty?
- Why do methods differ, and which differences were actually measured?
- What does one representative task require, return, and score?
- Where did methods fail, and is the cause provider, source, evaluator, or environment?
- How were all results independently checked, what changed, and what remains unresolved?
- What is needed to reproduce the current run?

When a report has a dedicated interpretation page, make it useful without a visit to the home page: place the current result and denominator near the opening, explain why it arose through an actual source/reference/output comparison, cover material issues for every method, and close with decisions and review scope. Use the supplied reference report for reading depth and hierarchy while keeping its data out of the new report. A renderer check should validate those facts and evidence targets from the current measured model and reject a missing method, broken example, misplaced timing context, or empty conclusion. A visual reader pass remains necessary because structural checks cannot judge whether prose is clear.

Show the task ID and run ID on result pages. Put the task requirement, usable reference scope, complete actual output, normalization, individual checks, and verdict rationale together. If the reference is shorter than the output or only covers anchors, state locally that the metric measures required coverage rather than whole-output equality.

Keep environment and reproduction together and deduplicated. Show repository/version, platform, method versions and routes, material runtime settings, credential names and acquisition locations, and current commands. Never expose secret values or machine-local paths.

For a multilingual report, generate every language from one semantic report model. Keep route inventory, section order, tables, values, filters and evidence links aligned one-to-one. Localize navigation and explanatory prose for the target language instead of translating mechanically; preserve task names, IDs, URLs, references, provider outputs and other measured evidence unless the benchmark explicitly evaluates translated content. A summary-only secondary language must be labeled as a summary, not presented as the full report.

Use a compact review page resembling release history: one card or row per review round with scope, findings, corrections, status, and remaining work. Keep per-result review evidence on result routes or in the evidence store; do not turn the review history into another task-result inventory.

Expose one logical result per method and task. A declared additional execution is an orchestration detail: preserve its immutable evidence, follow the score authority frozen before the run, and retain every execution's latency and cost separately from the primary timer defined by the evaluation contract. Do not split the public result table by execution ordinal unless that distinction is the preregistered research question.

Keep dataset inventory separate from measured results. Dataset pages explain the task, selection, reference and acceptance contract; result pages own method verdicts, timing, trajectories and run-derived solvability observations. Define participants as reproducible method stacks and render comparisons from a registry. When the participant set can grow beyond two, group each task's pass/fail participants and provide one baseline selector plus multi-select comparison participants and explicit outcome relations; optional pairwise aggregates may supplement this view. Do not hard-code a two-column comparison or keep adding one wide column per participant.

Name report tracks by the procedure a reader must distinguish, such as autonomous use versus deterministic trajectory replay, rather than by an internal module layer. Once a repository selects canonical track names, migrate code, commands, report routes and checks together; do not keep compatibility aliases unless external callers are explicitly in scope.

On result-detail pages, use the opening summary for the verdict, a concise failure locator, and short scalar facts. Put long evaluator messages, expected/actual payloads, traces, and logs in their dedicated evidence sections; never force diagnostic prose or raw JSON into a narrow KPI card.

For self-hosted web environments, include a deployment and network topology, image identities, included/excluded sites and compatibility layers. Distinguish configured preparation from independently bound preparation receipts. Split environment preparation, browser-session setup, agent run, tool-call wall time, evidence finalization and evaluator time at collection; label historical mixed timers by their actual boundaries and never call wrapper/CDP wall time pure browser-core speed.

Never publish user prompts, assistant reasoning, plans, internal notes, conversational back-and-forth, or tool-work narration. A formal report explains the benchmark and evidence; it is not the authoring notebook.

Write every title and paragraph for the reader, not as a reply to author feedback. Convert corrections into neutral artifact facts; never mention who requested a change, quote review conversation, or use a rebuttal-style heading when a descriptive heading works. Review history may record an objective finding and resolution, while release history records only reader-visible effects.

## Claims and rankings

Pair every ranking with its task family, denominator, acceptance meaning, a typical input-to-output route, and limits. Keep task acceptance separate from continuous quality, latency, cost, and title/body diagnostics. Do not call conditional averages global quality or explain a correlation as a verified mechanism.

Prefer concise tables for exact comparisons and prose for interpretation. Remove repeated caveats after placing each limitation where it changes interpretation. Run a factual pass before a prose/humanization pass; never let editing change numbers or scope.

For a standard numeric comparison, show mean, P50, and P95 when all three are useful. P50 is the median, so never present “median” and P50 as separate statistics. Declare whether higher or lower is better and visually mark only a unique column maximum/minimum within the displayed scope; a numerical highlight does not establish causal superiority or a whole-benchmark ranking. Conditional populations require visible counts, not suppression of their extrema. Leave ties, identifiers, call counts, and metrics without a universal direction neutral. Do not rank raw totals when unequal workloads make them non-comparable without also exposing the workload or normalized value.

Do not add a second static inventory when existing filters expose the same result slice. Keep one searchable result table and use focused summaries only when they add a distinct analytical relationship.

## Release gates

Classify the change before running gates:

- **Presentation-only:** prose, navigation, layout, style, or report assembly changed while the bound dataset, runner, scorer, evidence, scores, and denominator stayed fixed. Run targeted renderer tests, evidence/profile binding, local links/resources, privacy scan, destination/version checks, atomic publication, and exact deployed-package verification. Add responsive/visual checks only when layout or style changed.
- **Measurement-affecting:** dataset, task contract, runner, provider route, scorer, evidence, result, denominator, or claim scope changed. Run the repository's full locked code and evidence audit before the common publication gates.
- **Metadata-only:** release metadata or a verified remote version binding changed without changing distributable bytes. Validate the binding and target only; do not rebuild, republish, or rerun unrelated tests.

Do not run a broad test suite merely because a report is being published. The release command must state which mode ran and name the manual full-audit command when it was skipped.

Common publication gates:

1. rebuild from the current profile and frozen evidence when distributable bytes changed;
2. verify local links and packaged resources affected by the change;
3. scan the distributable for secrets, local paths, internal notes, and excluded raw evidence;
4. confirm the exact destination account, base URL, site ID, and expected current version;
5. publish atomically or create a new site when explicitly requested;
6. fetch the unauthenticated share URL and compare the deployed file tree or archive with the submitted package;
7. record a concise release entry with score/denominator effects and remaining limits.

The responsive/visual gate must cover every core route class, both languages, desktop and narrow widths, and light and dark themes. Check that short headers do not wrap one character per line, long cells receive deliberate width or scrolling, filters remain operable, code and evidence blocks have readable contrast, and text-heavy diagrams use scalable/vector rendering or retain a readable minimum width. A screenshot of only the home page is insufficient.

Render release history like a report, not a heading plus a raw table: show the current version summary first, keep evidence/handling/impact details expandable, retain older versions in a compact history, and provide the structured record download. Each entry states what changed, why, the formal handling, and the score, denominator, latency, evidence, and limitation effects.
Keep localized release text beside each structured release record and render every language from that record. Do not copy release-specific prose or historical measurements into report renderers or exact-string translation tables. Check the rendered release IDs, summary, effects and evidence links against the record; wording changes alone must not require a release-gate code edit.

Credentials belong in the approved secret store, never HTML or Git. If a service requires an account, list the account and official acquisition location in the report. Do not include live keys.

Publication authorization does not imply permission to overwrite another report. Bind the release target in the current profile and reject receipts for a different service or site. Return the public share link, stable site ID, PR/change link, verification result, and unresolved count.

## Shared presentation contract (all benchmark tracks)

These rules apply to every public benchmark report, regardless of provider, task family, or renderer. Track-specific evidence requirements extend them.

- **Component denominators:** distinguish “a reference exists” from “that reference contains this component.” If a denominator is the union of reference-side and output-side content, show the counted categories, zero-score additions and pre-scoring exclusions with measured counts. Do not describe an output-only component as a missing reference.
- **Metric labels:** use parallel names and units (Mean latency, P50 latency, P95 latency). Explain their common population once beside the table. Define P50 as the median and P95 as the 95th percentile at first use; do not attach “successful” only to the mean while silently applying it to all three. Label unavailable values separately from zero.
- **Highlights:** derive unique directional extrema from raw numeric values and the complete displayed participant registry. Ties remain neutral. Green denotes a column extreme in the declared scope, not statistical significance or an overall ranking. Keep the same markers in all languages, themes, and copies of the comparison. Conditional success coverage and post-hoc shared-success subsets remain explicit.
- **Table interaction:** choose controls by the table's reader task. Large result and dataset inventories must provide search or relevant filters plus stable sorting for decision-relevant columns. Compact comparison tables keep the complete participant set visible and provide accessible sorting for useful numeric columns; do not add filters that can hide participants and change the apparent comparison population. Small definition, provenance, or lookup tables may remain static. Sortable headers use buttons and `aria-sort`, cells expose raw machine-readable sort values where rendered text is formatted, unavailable values stay after real values in either direction, and ties retain source order. Sorting or filtering must never recompute directional highlights from the visible subset.
- **One fact, one owner:** use one result summary containing outcome and scalar facts. Distinguish request status, valid output, and task acceptance, but do not repeat them as several summary sections. Keep inventories compact and filterable. Put full output comparisons, scoring payloads, provenance, and logs behind labeled dialogs or disclosures; never flatten them into cards or inventory cells.
- **Repairable failures:** connect the failure location and program error category to exact error text, retained logs, relevant output or step, reviewed cause, and remediation evidence when recorded. Keep unknown fields explicitly unavailable; a warning or nearby successful action is not automatically the root cause. Preserve complete raw evidence, but do not duplicate a log stream in multiple visible sections.
- **Evidence interaction:** dialogs must have accessible names, close controls, Escape support, focus restoration, readable bounded scrolling, and usable narrow layouts. Verify a real failure from inventory to exact error/log and back, plus a successful output comparison. Test hosted navigation as well as local paths.
- **Executable protection:** release checks must recompute extrema (including ties/missing values), validate one summary and resolvable evidence controls, and reject failures without exact error evidence or an explicit unavailable state. Include negative controls that remove a highlight, mark the wrong/tied value, break a dialog target, or flatten evidence. Check labels, units, localization parity, and duplicate ownership, rather than only route counts or fixed prose snippets.

Repository adapter: `scripts/report_presentation_contract.py` provides track-independent checks over numeric cells marked with `data-metric`, `data-metric-direction`, and `data-sort`, and detail wrappers marked with `data-report-detail`. The current WebMainBench release gate invokes it. New or substantially rewritten tracks must integrate this checker (or equivalent semantic checks) in their own release command; adding a rule to this document is not sufficient. Existing tracks must not be claimed as covered until their gates actually invoke the checks.
