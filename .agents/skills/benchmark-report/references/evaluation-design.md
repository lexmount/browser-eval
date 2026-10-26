# Evaluation design

## Start from the decision

Define what a reader should be able to choose or conclude. Convert that into task families whose requirements differ for a reason. Do not compare percentages across families with different contracts as if they were one scale.

For each case, declare:

- stable task ID and family;
- input and permitted interaction scope;
- required output type, such as raw file, structured data, Markdown, rendered result, or action outcome;
- acceptance contract and separately reported quality metrics;
- reference source, observation time, scope, and volatility;
- timeout, call count, and any completion signal;
- fields intentionally not measured.

Document formats do not universally need Markdown. If the task is file retrieval, the raw PDF or Office file may be the product and should be validated by type, integrity, and task-specific contents. If the task is text extraction, define the normalized textual form. Convert to Markdown only when Markdown itself is the promised output or a declared common representation.

## Fairness means equivalent opportunity

Give each method the best supported route that satisfies the same external contract: native Markdown, structured endpoint, browser protocol, CLI, or a declared converter. Do not handicap a method to mimic another method's implementation.

Record, and where useful split, these sources of difference:

- remote service transit versus local process time;
- startup, download, installation, authentication, and one-time setup;
- cold and warm sessions;
- provider-side conversion versus client-side conversion;
- browser execution versus direct HTTP or product-specific extraction;
- account plan, region, concurrency, cache, and rate limits;
- measured wall time versus provider-billable time.

If these cannot be normalized, disclose them and avoid causal claims the design cannot support. A matched-task latency comparison may still be useful when labeled as end-to-end user-observed latency.

When comparing builds of one implementation, do not infer a code-induced latency change from two complete but time-separated batches. Predeclare the full input scope and selected round, alternate build order within every task, run serially without competing benchmark/build jobs, and retain every call with binary and input hashes. A subset is diagnostic only. If this experiment corrects the authoritative table, supersede the complete declared timing scope, preserve prior physical calls, and make report values and per-task details derive from the same selected calls. Enforce these checks in the release gate.

## References and volatile sources

Prefer invariant contracts for changing pages: stable labels, required fields, record shape, types, count bounds, uniqueness, and independently observed explanatory text. Do not freeze ranks, prices, member counts, or current titles unless the benchmark is explicitly a time-bound snapshot.

Ground truth must be independent of the provider under test. Retain source location, bytes or a durable observation, hash, capture method, and scope. Partial annotations support only the requirements they cover. They are not a full-body reference. When authoring or expanding a task, inspect its actual reference text against the reader's main content needs. A dashboard's labels do not establish its values; a feed's navigation does not establish its entries. Select applicable fact, body-region, record, pattern, resource and file checks through the shared scorer. Give the source-complete answer and a page shell or incomplete answer opposite outcomes before freezing the new version. Preserve earlier input versions.

## Machine-readable current profile

Keep one current profile that a fresh session can validate before running, scoring, rebuilding, or publishing. Include exact case IDs or their canonical digest, the ordered method/version set, run count, timeout, concurrency policy, output contract, reference state, review requirement, and release target. Fail closed on drift instead of reconstructing intent from old reports.

For native-content browser comparisons, use task-appropriate official interfaces and score the returned deliverable. A final URL or HTML language tag is diagnostic unless the benchmark explicitly evaluates that metadata. Do not repair missing text using JSON DOM conversion or external PDF/DOCX parsers. The declared Chromium HTML-to-Markdown exception does not authorize document parsers. Bind native script versions, generate the interface matrix from the runner route selector, and require route validation at aggregation and publication. Keep negative controls for wrong content alongside positive controls for correct content with missing metadata.

Keep browser engines distinct from execution configurations. Released and source-build variants use separate method identities in the execution registry and share the engine's native route constraints. Derive interface columns and participant counts from that registry. A strict native-mode marker selects default completion; it supplies no per-case selector or wait and must survive both dataset conversion and runner validation. Resource projection must preserve single URL values and tokenize only srcset candidates, including literal commas inside URLs. Protect these boundaries with runner, report and actual-browser controls.
