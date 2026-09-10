# Browser Use report parity matrix

This is an internal implementation checklist. It is not part of the public report.

| Reader question / function | Web Fetch reference | BrowserTool reference | Browser Use implementation |
|---|---|---|---|
| What was tested? | Searchable dataset inventory and per-case pages | Search, difficulty/site/action filters, per-task cards | Searchable 30-task inventory; per-task contract pages |
| What is the ground truth? | Full reference, stable anchors, field contract | Exact outputs, derivation, fixed reference actions | Official WebArena-Verified expected response and network-event contracts |
| Is there one correct path? | Not relevant to fetch | Yes, fixed harness action sequence | No canonical path; disclose outcome-based evaluation and separately label whether this run contains a successful trajectory proving current-snapshot solvability |
| What did the method actually do? | Provider output | Step-by-step actual BrowserTool calls | Full CowAgent BrowserTool trajectory projected from agent events |
| What did it return? | Full actual output | Saved outputs per step | Final agent response plus normalized evaluator actual values |
| Why did it pass/fail? | Rule-level checks and reference/output comparison | Output and process validation | Per-evaluator expected/actual/assertions/error with derived failure stage |
| What are the raw records? | Lazy complete output, downloadable evidence | Modal raw parameters/output/error | Sanitized response, evaluation, events, trajectory and evidence metadata downloads |
| What stays private? | Explicit publication privacy manifest | Public-web evidence only | HAR and Playwright trace remain private because they may contain session state; hashes and sizes are public |
| Can readers find failures? | Default-fail filters and search | Multi-dimensional result explorer | Default-fail result explorer with tier/site/type/engine/attempt/evaluator/search filters |
| Can readers compare engines per task? | Provider rows on dataset pages | Side-by-side reference/action table | Side-by-side primary result, actual final answer, failed evaluator and trajectory links |
| Can readers sort and preserve context? | Sortable rankings; loader restores filters/scroll | Search/filter and deep links | Sortable tables, URL-backed filters and sort, scroll restoration, counts and deep links; no virtual loader needed at this report size |
| Is review auditable? | Review rounds, protocol, coverage, disagreements | Programmatic result/process checks | Review protocol, full attempt coverage, per-attempt checks, disagreements and unresolved inventory |
| Are methodology and benchmark choice explained? | Track-local contracts and boundaries | Step/result metric definitions | Benchmark adoption, judge types, selection decision, deterministic outcome contract and limits |
| Are current report-only modules retained? | N/A | N/A | Benchmark landscape, Astra access probe, paired inference, autonomous-vs-fixed harness boundary |
| Is reproduction bounded? | Code/dataset identity and current commands | Source and raw data | Commit/model/reasoning/browser/site/evaluator identities and current commands |
| Is there a compact English entry point? | One-page English edition | No | One-page English results, scope, method, evidence, review, access and privacy summary |
| Can machine readers discover every artifact? | Root manifests and virtual artifact catalog | One bundled data index | Public evidence index maps all 70 attempt pages and five sanitized artifact types |

Implementation decisions:

- Do not invent a canonical action path for WebArena-Verified. Its ground truth is a conjunction of evaluator contracts.
- Do not treat an official contract as proof that the current site snapshot is solvable. Label tasks with a measured successful trajectory separately from tasks that have only the official contract.
- Publish benchmark answers because they are part of the public benchmark package; redact credentials and session-bearing material.
- Publish sanitized CowAgent events and evaluator records. Do not publish raw HAR or Playwright trace archives.
- Keep the direct static-site architecture. The projected report remains far below the reference report's virtual-file threshold, so a gzip virtual loader would add complexity without reader value.
