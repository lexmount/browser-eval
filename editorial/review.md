# Private editorial review

## Complete dynamic cohort acceptance (2026-09-03)

The dynamic supplement now contains four frozen tasks and all twelve methods,
48 unique calls. The hosted batch added 24 calls without retries. Every original
artifact hash, timing and run identity remains unchanged. General adjacent
Markdown-link normalization was then applied to all 1,728 stored outputs; its
189 metric differences and one task-pass change are recorded in
`research/markdown-boundary-score-audit.json`. Only Tavily's asynchronous quote
case changes task verdict: its distinct adjacent link labels retain boundaries.
Firecrawl's concatenated plaintext tags are not guessed back into separate tags.
No reference, fact requirement, forbidden marker or length threshold changed.
Independent review caught numeric continuity across adjacent links: `[-](u)[10](v)`,
`[10](u)[.5](v)` and `[10](u)[0](v)` must never match a positive `10`.
The normalizer retains those numeric/sign joins; focused regression tests and
a second full rescore verify this before publication.

The article keeps the Moli web/dynamic contrast as its opening, now backed by
the complete twelve-method supplement matrix. The independent article and
dashboard both state the actual 48-call coverage; historical tasks were not
rerun in this batch. The delayed-page paragraph derives its zero successes out
of twelve from records, not a fixed prose claim. Full normalization rules stay
in Methodology rather than duplicating an implementation changelog in the essay.
The Humanizer pass preserved concrete limits and removed the potentially
ambiguous partial-provider wording. Desktop and 390px article screenshots were
inspected; the matrix remains inside its horizontal scroll container on narrow
screens. The local QA wrapper was removed before packaging.

This file is source-only and must not enter the report bundle. No publication or commit was performed by this editor.

## Duplication audit and content ownership (2026-09-03)

Re-read AGENTS.md, the complete local benchmark-editorial skill and both references, and Humanizer in full for this turn. Used them to judge whether repeated material helps an independent reader, not to impose a repetition/word-count threshold.

### Read-only coverage

Traversed and parsed all 1,859 generated HTML pages before changes: root 1; accounts 1; adoption 1; analysis 1; attempts 1,692; datasets 145; environment 1; guide 2; integration 1; methodology 1; providers 12; reproduce 1. Extracted headings, prose and repeated paragraphs across the entire corpus. Read the substantive top-level prose, method/interface structure, and representative case/attempt pages in detail, including ecommerce-list, dynamic-products-api and Moli dynamic-quotes-api. Repeated task prompts across 1,300 body-related pages and repeated measurement definitions in result tables are necessary local context, not 1,300 independent editorial defects. The report corpus was not edited or rebuilt in place by this editor.

### Ownership map

| Surface / source owner | Primary responsibility | What the independent article retains |
|---|---|---|
| Homepage, report.py (main) | Coverage, overview and complete quantitative rankings | Only comparisons that drive the argument; no second all-track winners table. Main removed duplicated “主要结论” and added method coverage to cards. |
| Methodology and task_pass_guide, report.py/report_components.py (main) | Full task contracts, matching rules, metric formulas, sampling boundaries | One short pass/F1 definition, enough to understand the rank reversal and intervention. Main changed rule links to methodology task anchors, so dashboard readers need not read the article. |
| datasets/index and per-case pages (main) | Reference scope, source provenance, permitted fact expressions, all methods on each case | Selected case facts with direct evidence links; book fact table remains optional detail. |
| analysis and attempts (main) | Filtering, diagnostics, timings, full output and per-fact results | Failure partition, observed Loading output, price intervention and specific counterexamples, not an explanation of filter controls. |
| providers and adoption/method introduction (main) | Configurations, native interfaces, examples, official capabilities and adoption evidence | Current extractor mechanism only where needed to explain its observed trade-off. No API catalog or popularity commentary. |
| environment (main) | Full execution manifests, timing windows and network/session conditions | Historical figure's time window; batch differences beside matched-latency inference. |
| reproduce and accounts (main) | Rebuild steps, downloads, account setup | Evidence/reproduction links, no duplicated instructions. |
| Integration page (main, removed) | Previously repeated four generic boundaries without an independent workflow | No replacement article checklist. Actual interface material remains in method introduction. Removal and nav updates belong to main. |
| guide/index and guide/article, template + explainer (this editor) | Explain surprising results, actual mechanisms, rival explanations and the decision they change | Moli web/dynamic contrast; delayed/placeholder completion; CF/Exa reversal; CSS low-purity case; price intervention; extractor probe; initial-reference recoverability; Cow/Moli overlap with paired latency; same-site body/resource mismatch; common challenge gaps and file-verification limit. |

### Decisions applied, not blanket deduplication

- Reduced the quality comparison from six methods to CF, Exa and CowAgent: the first two establish the reversal; the third connects it to the low-F1 passing case and extractor probe. Removed methods are still fully present in computed evidence and the dashboard.
- Removed the article's other-track winners table. Its maxima merely repeated homepage rankings and did not explain a choice. Replaced it with linked CowAgent Books to Scrape body/resource outcomes computed from the same input records; this demonstrates the contract change on one site rather than reciting leaders.
- Kept the dynamic case-by-method matrix: it shows distinct failures that an aggregate score hides. Kept failure-set partition, rescue-case table, matched-success latency and all underlying IDs. These are original decision analyses, not redundant ranking lists.
- Kept minimal metric definitions and a concrete scope/denominator beside each comparison. Removed the reference-error-word exception and Unicode/Markdown matching inventory from article prose because those are full-protocol material, available in methodology. The article still states that pass rules have no F1 gate and overlap is not semantic correctness.
- Put the full book fact-expression table in optional details; the actual price intervention, original/altered results and implication remain visible without opening it. No essential conclusion depends on an accordion.
- Removed prose that restated figure counts, chapter-tour transitions, the repeated dynamic-provider aggregate list, and the generic fallback capability warning. The new dynamic closing names the engineering decision: where data arrives and what permits delivery.
- Retained file-verification missingness and complex-site validity boundaries because deleting them would change the meaning of the results. Condensed their definitions and finished with the actual common-gap decision, not generic advice.
- Renderer coverage continues to derive from actual track records; added a six-method synthetic rendering test. No missing method is assigned a failure and no historical statistic changes when supplement coverage expands.

### Ownership and invariants

This turn edits only harness/web_fetch/templates/interpretation.html, harness/web_fetch/report_explainer.py, editorial/review.md, and permitted semantic tests in tests/test_report_explainer.py. report.py, report_components.py, report_methods.py, styles, dataset, scoring, runners and packaging are owned elsewhere and were not edited or reverted. No provider calls, publication or commits. All collected statistics and the JSON evidence schema remain intact; changes select and explain existing evidence, with a computed body/resource cross-reference replacing a duplicate ranking.

### Final reading and verification for the duplication pass

Read the generated standalone article in order with optional details removed. Its argument still includes scope, observed difference, concrete case, mechanism and decision without needing the dashboard. Removed residual “below we will see” transitions, a fact-table announcement that no longer fit optional details, and an unnecessary repeated historical-causality warning in the media paragraph (that paragraph now makes no causal claim). The main historical fingerprint limitation stays beside the mechanism experiment.

Validation: 15 explainer tests passed, including six-method supplement rendering and preservation of selected argument evidence; full current-worktree suite 270 passed, 13 skipped, 57 subtests passed. Both JavaScript suites passed. Isolated build at /private/tmp/editorial-dedup-qa-vzaytthf/report passed integrity and link checks: 144 cases, 1,692 records, 1,858 pages, zero broken local links. The difference from the 1,859-page audit inventory is main's Integration removal. Subsequent provider runs and publication remain main-owned; these validation counts describe the captured inputs, not a promise that the pending supplement is complete. No new visual design or CSS change in this turn; main retains final visual QA.

## Read before editing

Read AGENTS.md, benchmark-editorial/SKILL.md, both complete local references (editorial-standard.md and html-delivery.md), and the complete Humanizer skill. The initial combined tool output truncated Humanizer; it was re-read in two complete ranges before editing. Also personally read the Browser Use SOTA article and the internal income-certificate RSI reference, extracting the article text rather than accepting truncated HTML. Read the linked Sentry, blog-writing-specialist, results-report and HTML research-report guides; their marketing voice, external runtimes and SEO structures were not activated. No project orchestration was installed globally.

## Argument and substantive edits

The opening now starts with the same configured Moli's contrasting web/dynamic results, both computed, rather than an abstract full of dataset counts. It proceeds to normal-response failures, then the separate dynamic supplement, then the disagreement between task pass and F1, an actual price intervention, extractor mechanics and failure overlap. The decision is a controlled content-validated fallback trial, not a production guarantee. The shared authored body lives in interpretation.html; figures, dataset-specific examples and evidence are computed by report_explainer.py.

Removed the previous dashboard-reading tour, seven-step glossary structure, FAQ-like questions, public styling-reference note, internal reference URL, and generic final checklist. Reduced repeated caution by placing limitations beside the claim they restrict. Kept the actual case examples and final complex-site decision rather than ending with a slogan. No invented first-person experience, attributed human author, new provider calls or score edits.

## Claim ledger

| Claim | Evidence / calculation | Interpretation and boundary |
|---|---|---|
| Normal responses account for much of historical task failure | Report-builder normalized inputs; historical = track != dynamic; request-ok AND NOT task_passed, divided by all failed records | A content contract is needed after request success. Missing verification is not automatically a provider defect. |
| Historical and dynamic cohorts differ | Partition cases and records by track; count actual providers and records independently | Never use total cases × all registered methods to imply a balanced matrix. Missing dynamic providers are unmeasured. |
| Web pass and F1 order can reverse | Per-provider web task_passed counts and mean of persisted per-record F1 values with non-null F1 | Same scope, different objectives. No new F1 gate; no average-of-averages substitution. |
| Low-F1 passing CowAgent case | Minimum persisted F1 among passing web CowAgent rows; retrieve recall, precision and case/attempt IDs | High reference recall does not establish clean output or semantic correctness. |
| A price replacement changes content acceptance | ecommerce-list artifact; evaluate_content before and after declared 51.77 → 999.99 intervention; save checksum and original fact results | Memory copy only. Not another benchmark observation or evidence of whole-page correctness. |
| CowAgent keeps navigation/footer but loses attributes | research/cowagent-text-probe.json, synthetic fixture and reproducible pure extractor test | Current function behavior on its fixture. Historical calls lack per-attempt source fingerprint, so no quantified historical causal contribution. |
| Cow/Moli overlap and matched latency | Single observation per provider/case; partition matched IDs into both, Cow-only, Moli-only, neither; median on common successes with both timings | Offline union, not executed fallback. Failed waits excluded; batches differ. No monetary saving or guaranteed latency. Repeated observations disable pairing rather than reward retries. |
| Initial HTML versus asynchronous content | initial-reference-audit.json verifies case IDs, every current web reference hash and actual web source hash before use; count exact reconstructions from per-case booleans | Public evidence includes only whitelisted IDs/hashes/outcomes, no absolute paths. Audited/current dataset hashes are separately labeled because other-track rules changed afterward; the web claim remains bound to current web cases and reference/source bytes. Exact frozen-reference recovery is not visible static HTML or a JS necessity test. |
| Moli Loading output | Read normalized failing dynamic Moli artifact; emit only if literal Loading... is present | Explain the observed return, not a speculative internal timing cause. |
| Dynamic completion has to inspect fields | dynamic-products-api source_benchmark.expected_entries; independent completion conditions and fresh-navigation checks; initial placeholder-card finding | Counting DOM cards alone falsely completes empty placeholders. Reference recapture checks frozen reference; it does not add waiting to provider calls. |
| Delayed page and network-idle | Delayed case plus scripts/oss/crawl4ai_worker.py wait_until=networkidle and saved output | A readiness boundary problem; no measured ablation proving a replacement waiting policy's gain. |
| Complex popular-site union remains incomplete | challenge track per-provider pass counts and union of passing case IDs | Access restrictions do not invalidate tasks. New entities without independent verification are unverified, not fabricated. Does not certify current rankings/prices. |
| Raw-file verification is missing | Count file-kind rows missing raw_file_pass separately from verified passes | Missing runner acceptance evidence is not proof products cannot download files. |

## Humanizer audit and final pass

Draft audit found a statistics-heavy opening, a generic end-of-article checklist, repeated disclaimers, and headings that followed dashboard sections. Revised the lead to the measured Moli contrast, moved scope detail to the first results figure and dynamic section, removed the generic ending, and attached limits directly to concrete mechanisms. Technical definitions remain only where needed to explain F1, the price intervention, completion or file delivery. Kept independent API access as a real alternative; removed any framing that equated embedded payload with static visible content. Core uncertainty remains explicit and is not softened into marketing certainty.

## Implementation / validation handoff

- Existing render_explainer signature retained; no changes to report.py, dataset, runner or packaged loader by this editor.
- Standalone guide/article.html has the exact same article element, inline shared STYLE, no JavaScript/CDN/font dependency. Integrated-only evidence panel has native relative download links; shared-body absolute links target HTML hub routes, never JSON-as-page routes.
- Semantic tests cover derived counts, cohort separation, F1 aggregation, overlap partitions/latency, mutated input, escaped labels, validated IDs, same-body export, no external dependencies, native downloads, anchors and table numbering. Price counterfactual and extractor-probe regression tests retained.
- At the first ready handoff: 12 explainer tests passed; 44 related report/security/end-to-end tests and 19 subtests passed. Main owns desktop/mobile/print visual QA, final merged-data report build and publication. Do not treat these tests as proof of literary or visual quality.
- Full validation before final audit binding: 245 passed, 13 skipped, 57 subtests passed; both JavaScript test files passed. An isolated current-data build passed report integrity (144 tasks, 1692 records, 1859 HTML pages) and zero broken local links. Final audit-binding test added afterward and rerun with the explainer tests. The integrated-only panel supports native HTML/JSON downloads; shared-body links route to that HTML panel.
