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

Use a compact review page resembling release history: current reviewed coverage and unresolved count, then one card or row per review round with scope, findings, corrections, and remaining work. Do not publish agent work logs, giant execution manifests, or downloads merely because the evidence exists.

## Claims and rankings

Pair every ranking with its task family, denominator, acceptance meaning, a typical input-to-output route, and limits. Keep task acceptance separate from continuous quality, latency, cost, and title/body diagnostics. Do not call conditional averages global quality or explain a correlation as a verified mechanism.

Prefer concise tables for exact comparisons and prose for interpretation. Remove repeated caveats after placing each limitation where it changes interpretation. Run a factual pass before a prose/humanization pass; never let editing change numbers or scope.

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

Credentials belong in the approved secret store, never HTML or Git. If a service requires an account, list the account and official acquisition location in the report. Do not include live keys.

Publication authorization does not imply permission to overwrite another report. Bind the release target in the current profile and reject receipts for a different service or site. Return the public share link, stable site ID, PR/change link, verification result, and unresolved count.
