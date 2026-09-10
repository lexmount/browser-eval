# Report and release

## Reader-facing structure

Organize around reader questions, not storage tables. A useful report usually makes these answers easy to find:

- What was compared, on which tasks and versions?
- What is the headline result, with denominator and uncertainty?
- Why do methods differ, and which differences were actually measured?
- What does one representative task require, return, and score?
- Where did methods fail, and is the cause provider, source, evaluator, or environment?
- How were all results independently checked, what changed, and what remains unresolved?
- What is needed to reproduce the current run?

Show the task ID and run ID on result pages. Put the task requirement, usable reference scope, complete actual output, normalization, individual checks, and verdict rationale together. If the reference is shorter than the output or only covers anchors, state locally that the metric measures required coverage rather than whole-output equality.

Keep environment and reproduction together and deduplicated. Show repository/version, platform, method versions and routes, material runtime settings, credential names and acquisition locations, and current commands. Never expose secret values or machine-local paths.

For a multilingual report, generate every language from one semantic report model. Keep route inventory, section order, tables, values, filters and evidence links aligned one-to-one. Localize navigation and explanatory prose for the target language instead of translating mechanically; preserve task names, IDs, URLs, references, provider outputs and other measured evidence unless the benchmark explicitly evaluates translated content. A summary-only secondary language must be labeled as a summary, not presented as the full report.

Use a compact review page resembling release history: one card or row per review round with scope, findings, corrections, status, and remaining work. Keep per-result review evidence on result routes or in the evidence store; do not turn the review history into another task-result inventory.

Expose one logical result per method and task. A declared additional execution is an orchestration detail: preserve its immutable evidence, follow the score authority frozen before the run, and add every execution's latency and cost. Do not split the public result table by execution ordinal unless that distinction is the preregistered research question.

Keep dataset inventory separate from measured results. Dataset pages explain the task, selection, reference and acceptance contract; result pages own method verdicts, timing, trajectories and run-derived solvability observations. Define participants as reproducible method stacks and render comparisons from a registry. When the participant set can grow beyond two, group each task's pass/fail participants and provide one baseline selector plus multi-select comparison participants and explicit outcome relations; optional pairwise aggregates may supplement this view. Do not hard-code a two-column comparison or keep adding one wide column per participant.

On result-detail pages, use the opening summary for the verdict, a concise failure locator, and short scalar facts. Put long evaluator messages, expected/actual payloads, traces, and logs in their dedicated evidence sections; never force diagnostic prose or raw JSON into a narrow KPI card.

For self-hosted web environments, include a deployment and network topology, image identities, included/excluded sites and compatibility layers. Distinguish configured preparation from independently bound preparation receipts. Split environment preparation, browser-session setup, agent run, tool-call wall time, evidence finalization and evaluator time at collection; label historical mixed timers by their actual boundaries and never call wrapper/CDP wall time pure browser-core speed.

Never publish user prompts, assistant reasoning, plans, internal notes, conversational back-and-forth, or tool-work narration. A formal report explains the benchmark and evidence; it is not the authoring notebook.

Write every title and paragraph for the reader, not as a reply to author feedback. Convert corrections into neutral artifact facts; never mention who requested a change, quote review conversation, or use a rebuttal-style heading when a descriptive heading works. Review history may record an objective finding and resolution, while release history records only reader-visible effects.

## Claims and rankings

Pair every ranking with its task family, denominator, acceptance meaning, a typical input-to-output route, and limits. Keep task acceptance separate from continuous quality, latency, cost, and title/body diagnostics. Do not call conditional averages global quality or explain a correlation as a verified mechanism.

Prefer concise tables for exact comparisons and prose for interpretation. Remove repeated caveats after placing each limitation where it changes interpretation. Run a factual pass before a prose/humanization pass; never let editing change numbers or scope.

For a standard numeric comparison, show mean, P50, and P95 when all three are useful. P50 is the median, so never present “median” and P50 as separate statistics. Declare whether higher or lower is better and visually mark only a unique, directionally valid winner; leave ties, identifiers, call counts, and metrics without a universal direction neutral. Do not rank raw totals when unequal workloads make them non-comparable without also exposing the workload or normalized value.

Do not add a second static inventory when existing filters expose the same result slice. Keep one searchable result table and use focused summaries only when they add a distinct analytical relationship.

## Release gates

Before publication:

1. run the repository's locked tests and evidence integrity checks;
2. rebuild from the current profile and frozen evidence;
3. verify every local link and packaged resource, including narrow-screen and representative detail pages;
4. scan the distributable for secrets, local paths, internal notes, and excluded raw evidence;
5. confirm the exact destination account, base URL, site ID, and expected current version;
6. publish atomically or create a new site when explicitly requested;
7. fetch the unauthenticated share URL and compare the deployed file tree or archive with the submitted package;
8. record a concise release entry with score/denominator effects and remaining limits.

The responsive/visual gate must cover every core route class, both languages, desktop and narrow widths, and light and dark themes. Check that short headers do not wrap one character per line, long cells receive deliberate width or scrolling, filters remain operable, code and evidence blocks have readable contrast, and text-heavy diagrams use scalable/vector rendering or retain a readable minimum width. A screenshot of only the home page is insufficient.

Render release history like a report, not a heading plus a raw table: show the current version summary first, keep evidence/handling/impact details expandable, retain older versions in a compact history, and provide the structured record download. Each entry states what changed, why, the formal handling, and the score, denominator, latency, evidence, and limitation effects.

Credentials belong in the approved secret store, never HTML or Git. If a service requires an account, list the account and official acquisition location in the report. Do not include live keys.

Publication authorization does not imply permission to overwrite another report. Bind the release target in the current profile and reject receipts for a different service or site. Return the public share link, stable site ID, PR/change link, verification result, and unresolved count.
