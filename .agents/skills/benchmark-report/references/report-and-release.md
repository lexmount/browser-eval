# Report and release

## Template discovery

Run `make report-template TRACK=webfetch` (or `trajectory-replay` / `browser-use` / `web-search`) before authoring. The registry returns the maintained owner, version-pinned example, build and check commands; a private example link comes from the `example_env` variable and is empty while that variable is unset. A reference report supplies presentation conventions, never evidence or the new publication target. Extend the owner instead of copying its HTML into a temporary renderer.

A new report type follows the new-track rule in `AGENTS.md`: persist a reusable example and scenario-based positive/negative tests and register its owner and release gate before generating the requested report. Later corrections update the owner and relevant rules when supported by evidence; do not clone a template per run or embed report-specific conclusions in instructions.

Web Fetch diagnostic models, compiled reruns and diagnostic releases are in the [Web Fetch guide](../../../../src/harness/web_fetch/README.md#certified-cohorts-and-report-builds).

## Language generation

[Report languages](../../../../docs/report-hosting.md#report-languages) owns language selection, editions and the not-generated notice.

## Reader-facing structure

[Presentation quality](presentation-quality.md#reader-facing-structure) owns page structure, failure chapters, language passes and interpretation pages. The rules below add multilingual, review and result-page requirements.

For a multilingual report, generate every language from one semantic report model. Keep route inventory, section order, tables, values, filters and evidence links aligned one-to-one. Localize navigation and explanatory prose for the target language instead of translating mechanically; preserve task names, IDs, URLs, references, provider outputs and other measured evidence unless the benchmark explicitly evaluates translated content. A summary-only secondary language must be labeled as a summary, not presented as the full report.

Use a compact review page resembling release history: one card or row per review round with scope, findings, corrections, status, and remaining work. Keep per-result review evidence on result routes or in the evidence store; do not turn the review history into another task-result inventory.

Expose one logical result per method and task. A declared additional execution is an orchestration detail: preserve its immutable evidence, follow the score authority frozen before the run, and retain every execution's latency and cost separately from the primary timer defined by the evaluation contract. Do not split the public result table by execution ordinal unless that distinction is the preregistered research question.

Keep dataset inventory separate from measured results. Dataset pages explain the task, selection, reference and acceptance contract; result pages own method verdicts, timing, trajectories and run-derived solvability observations. Define participants as reproducible method stacks and render comparisons from a registry, never hard-coded. Browser Use renders one same-task matrix with a generated column per configuration. Every other track with more than two participants provides one baseline selector plus multi-select comparison participants and explicit outcome relations; Browser Trajectory Replay also groups each task's pass/fail participants, and optional pairwise aggregates may supplement that view.

Name report tracks by the procedure a reader must distinguish, such as autonomous use versus deterministic trajectory replay, rather than by an internal module layer. Once a repository selects canonical track names, migrate code, commands, report routes and checks together; do not keep compatibility aliases unless external callers are explicitly in scope.

On result-detail pages, use the opening summary for the verdict, a concise failure locator with failed-rule labels, and short scalar facts. Put long evaluator messages, expected/actual payloads, traces, and logs in their dedicated evidence sections; never force diagnostic prose or raw JSON into a narrow KPI card.

For self-hosted web environments, include a deployment and network topology, image identities, included/excluded sites and compatibility layers. Distinguish configured preparation from independently bound preparation receipts, and claim per-execution reset/readiness only when a preparation receipt binds reset, readiness and application health. Split environment preparation, browser-session setup, agent run, tool-call wall time, evidence finalization and evaluator time at collection; label historical mixed timers by their actual boundaries and never call wrapper/CDP wall time pure browser-core speed.

Never publish user prompts, assistant reasoning, plans, internal notes, conversational back-and-forth, references to parallel sessions, or tool-work narration. A formal report explains the benchmark and evidence; it is not the authoring notebook.

Write every title and paragraph for the reader, not as a reply to author feedback. Convert corrections into neutral artifact facts; never mention who requested a change, quote review conversation, or use a rebuttal-style heading when a descriptive heading works. Review history may record an objective finding and resolution, while release history records only reader-visible effects.

## Claims and rankings

Pair every ranking with its task family, denominator, acceptance meaning, a typical input-to-output route, and limits. Keep task acceptance separate from continuous quality, latency, cost, and title/body diagnostics. Do not call conditional averages global quality or explain a correlation as a verified mechanism.

Prefer concise tables for exact comparisons and prose for interpretation. Remove repeated caveats after placing each limitation where it changes interpretation. Run a factual pass before a prose/humanization pass; never let editing change numbers or scope.

For a standard numeric comparison, show mean, P50, and P95 when all three are useful. P50 is the median, so never present “median” and P50 as separate statistics. Declare whether higher or lower is better and visually mark only a unique column maximum/minimum within the displayed scope; a numerical highlight does not establish causal superiority or a whole-benchmark ranking. Conditional populations require visible counts, not suppression of their extrema. Leave ties, identifiers, call counts, and metrics without a universal direction neutral. Do not rank raw totals when unequal workloads make them non-comparable without also exposing the workload or normalized value.

Do not add a second static inventory when existing filters expose the same result slice. Keep one searchable result table and use focused summaries only when they add a distinct analytical relationship.

## Release gates

Local reports do not require publication. Apply the destination and upload gates below only when the user requests sharing and authorizes the selected host.

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

The release reader pass runs when measured results, rendered layout or content hierarchy changed. It renders each top-level navigation page once at the standard desktop width in every explicitly generated edition. It checks the report's reading order, visible values, data-bearing visuals, tables and horizontal overflow. It does not repeat detail pages, themes, viewports or interaction tours. Shared renderer/component tests own narrow-width and theme behavior; deterministic generation-time tests own route completeness, links, filters, pagination, dialogs and evidence loading. A host-specific nested-path contract gets one representative local topology check, never a remote click-through. Routine report checks stay fast and deterministic and launch no browser; the release pass is a focused content-and-layout review, not a second interaction test suite.

Visual gates that already go further keep their present scope: Browser Use and WebArena replay receipts bind their schema, viewports and checker hashes, and the WCXB check, the published-snapshot audit and the Lexbench `ui_check.js` gate cover their own themes and widths. Existing receipts must keep verifying. New gates follow the pass above; narrowing an existing gate needs a new receipt schema and a code change, not a rule edit.

Render release history like a report, not a heading plus a raw table: show the current version summary first, keep evidence/handling/impact details expandable, retain older versions in a compact history, and provide the structured record download. Each entry states what changed, why, the formal handling, and the score, denominator, latency, evidence, and limitation effects.
Keep localized release text beside each structured release record and render every language from that record. Do not copy release-specific prose or historical measurements into report renderers or exact-string translation tables. Check the rendered release IDs, summary, effects and evidence links against the record; wording changes alone must not require a release-gate code edit. Browser Use and Browser Trajectory Replay keep their existing structured release models.

`AGENTS.md` owns credential sources; credentials never enter HTML or Git. If a service requires an account, list the account and official acquisition location in the report.

Publication authorization does not imply permission to overwrite another report. Bind the release target in the current profile and reject receipts for a different service or site. Return the public share link, stable site ID, PR/change link, verification result, and unresolved count.

## Shared presentation contract (all benchmark tracks)

These rules apply to every public benchmark report, regardless of provider, task family, or renderer. Track-specific evidence requirements extend them.

- **Component denominators:** distinguish “a reference exists” from “that reference contains this component.” If a denominator is the union of reference-side and output-side content, show the counted categories, zero-score additions and pre-scoring exclusions with measured counts. Do not describe an output-only component as a missing reference.
- **Metric labels:** use parallel names and units (Mean latency, P50 latency, P95 latency). Explain their common population once beside the table. Define P50 as the median and P95 as the 95th percentile at first use; do not attach “successful” only to the mean while silently applying it to all three. Label unavailable values separately from zero.
- **Highlights:** derive unique directional extrema from raw numeric values and the complete displayed participant registry. Ties remain neutral. Green denotes a column extreme in the declared scope, not statistical significance or an overall ranking. Keep the same markers in all languages, themes, and copies of the comparison. Conditional success coverage and post-hoc shared-success subsets remain explicit.
- **Table interaction:** choose controls by the table's reader task. Large result and dataset inventories must provide search or relevant filters plus stable sorting for decision-relevant columns. Compact comparison tables keep the complete participant set visible and provide accessible sorting for useful numeric columns; do not add filters that can hide participants and change the apparent comparison population. Small definition, provenance, or lookup tables may remain static. Sortable headers use buttons and `aria-sort`, cells expose raw machine-readable sort values where rendered text is formatted, unavailable values stay after real values in either direction, and ties retain source order. Sorting or filtering must never recompute directional highlights from the visible subset.
- **One fact, one owner:** use one result summary containing outcome and scalar facts. Distinguish request status, valid output, and task acceptance, but do not repeat them as several summary sections. Keep inventories compact and filterable. Put full output comparisons, scoring payloads, provenance, and logs behind labeled dialogs or disclosures; never flatten them into cards or inventory cells.
- **Repairable failures:** connect the failure location and program error category to exact error text, retained logs, relevant output or step, reviewed cause, and remediation evidence when recorded. Keep unknown fields explicitly unavailable; a warning or nearby successful action is not automatically the root cause. Preserve complete raw evidence, but do not duplicate a log stream in multiple visible sections.
- **Evidence interaction:** dialogs must have accessible names, close controls, Escape support, focus restoration, readable bounded scrolling, and usable narrow layouts. Exercise maintained renderer components with representative success and failure fixtures: inventory to exact error/log, keyboard dismissal with focus restoration, and a successful output comparison. Test host-specific nested navigation locally. Keep these behavior tests separate from the top-level release reader pass.
- **Executable protection:** release checks must recompute extrema (including ties/missing values), validate one summary and resolvable evidence controls, and reject failures without exact error evidence or an explicit unavailable state. Include negative controls that remove a highlight, mark the wrong/tied value, break a dialog target, or flatten evidence. Check labels, units, localization parity, and duplicate ownership, rather than only route counts or fixed prose snippets.

Repository adapter: `src/harness/reporting/presentation.py` provides track-independent checks over numeric cells marked with `data-metric`, `data-metric-direction`, and `data-sort`, and detail wrappers marked with `data-report-detail`. The Web Fetch, WCXB, WebMainBench and Web Search release gates invoke it, as do the Web Fetch diagnostic and published-snapshot checks. The Browser Use and Trajectory Replay gates use only its `visible_text` helper, and Lexbench has its own verifier, so they are not covered by it. New or substantially rewritten tracks must integrate this checker (or equivalent semantic checks) in their own release command; adding a rule to this document is not sufficient. Existing tracks must not be claimed as covered until their gates actually invoke the checks.

Shared gate flow: `src/harness/reporting/release_check.py` owns what every release check repeats: opening a link-free report, reading pages through the presentation rules, naming language editions and binding report identity. Each track keeps its own rules and negative controls. Do not route a module into it when a visual receipt or manifest records that module's hash (`checker_sources`, `producer_sources`, `renderer_sha256`).

Keep unpublished correction notes outside public release history. Schema 3 requires an explicit publication state for every release; an unmarked record fails the build. When a structured release is staged, mark it `publication_state: unpublished`; the public history selector and revision-binding gate must exclude it. Preserve its private correction receipt and measured calls.
