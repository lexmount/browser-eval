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

Give each method the best supported route that satisfies the same external contract: native Markdown, structured endpoint, browser protocol, CLI, or a declared converter. Do not handicap a method to mimic another method's implementation. Equalize the task, inputs, output contract, call budget and scoring; do not require identical internals.

Record, and where useful split, these sources of difference:

- remote service transit versus local process time;
- startup, download, installation, authentication, and one-time setup;
- cold and warm sessions;
- provider-side conversion versus client-side conversion;
- browser execution versus direct HTTP or product-specific extraction;
- account plan, region, concurrency, cache, and rate limits;
- measured wall time versus provider-billable time.

If these cannot be normalized, disclose them and avoid causal claims the design cannot support. A matched-task latency comparison may still be useful when labeled as end-to-end user-observed latency.

A report claiming a before/after build speed difference must use a predeclared paired protocol; otherwise do not claim a difference. Two complete but time-separated batches cannot supply the authoritative difference. Predeclare the full input scope and select the round before seeing results, alternate build order within every task, run serially on a quiet host without competing benchmark/build jobs, and retain every call with binary and input hashes. A subset is diagnostic only. If this experiment corrects the authoritative table, retain the earlier timing privately, supersede the complete declared timing scope, preserve prior physical calls, and make report values and per-task details derive from the same selected calls. That report's release check must reject an absent or partial timing receipt.

## References and volatile sources

Select tasks by independently verified page and reference validity, never by a
participant's accessibility or score. A checker timeout, rate limit, security
block, or HTTP error leaves validity unresolved; it is not proof that the page
has disappeared. Retain that candidate for independent verification rather than
silently replacing it with an easier page. Exclude or correct a task only with
source evidence of removal, wrong content, or erroneous/stale annotations, and
apply that decision to every participant. A provider's failure on a valid task
remains a measured failure.

Prefer invariant contracts for changing pages: stable labels, required fields, record shape, types, count bounds, uniqueness, and independently observed explanatory text. Do not freeze ranks, prices, member counts, or current titles unless the benchmark is explicitly a time-bound snapshot.

Ground truth must be independent of the provider under test. Retain source location, bytes or a durable observation, hash, capture method, and scope. Partial annotations support only the requirements they cover. They are not a full-body reference. When authoring or expanding a task, inspect its actual reference text against the reader's main content needs. A dashboard's labels do not establish its values; a feed's navigation does not establish its entries. Select applicable fact, body-region, record, pattern, resource and file checks through the shared scorer. Give the source-complete answer and a page shell or incomplete answer opposite outcomes before freezing the new version. Preserve earlier input versions.

## Machine-readable current profile

Keep one current profile that a fresh session can validate before running, scoring, rebuilding, or publishing. Include exact case IDs or their canonical digest, the ordered method/version set, run count, timeout, concurrency policy, output contract, reference state, review requirement, and release target. Fail closed on drift instead of reconstructing intent from old reports.

The [live-URL guide](../../../../src/harness/web_fetch/HEADLESS.md#native-browser-routes) owns native browser route rules.

## Hosted copies of frozen inputs

For Web Extract snapshot hosting, use the selected HF edition as the input
authority. Resolve the requested tag or latest once, freeze its full commit, and
keep historical runs pinned. Default to Cloudflare but accept other public origins.
Public HTML hosting is optional: reuse each dataset's independent service or
verify a caller-supplied origin. A navigation page is optional and owns no inputs.
Managed snapshots declare their HF repository, full source commit and dataset/file
inventory hashes in one public manifest. Identify and recheck the dataset through
that manifest; do not download every HTML page to determine a snapshot version.
Keep an explicit byte audit separate from normal version checks. An unversioned
copy needs an approved replacement deployment. A mismatch
must produce a target- and version-bound proposal without remote writes. Deploy
only after explicit approval, reject a proposal if its HF commit or target has
changed, and retain previous deployment URLs. Hosting metadata may accompany HF
originals, but it must never replace them or silently advance an active cohort.
