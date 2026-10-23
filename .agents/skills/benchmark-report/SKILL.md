---
name: benchmark-report
description: Design, run, audit, revise, and release evidence-backed benchmark reports that compare systems, providers, models, tools, or versions. Use for benchmark methodology, scoring, result dashboards, reproducibility, independent result review, or publication. Not for a standalone editorial article without benchmark maintenance.
---

# Benchmark report

Produce a report whose conclusions can be traced to frozen inputs, measured outputs, scoring rules, and the declared result-review coverage. Optimize for a reader deciding what the benchmark means, not for preserving the author's workflow.

Read the repository's `AGENTS.md` first. Project rules override this general skill. Before changing a benchmark, read [evaluation design](references/evaluation-design.md). Before reviewing scores or deciding whether to rerun, read [audit and revision](references/audit-and-revision.md). Before building or publishing a report, read [report and release](references/report-and-release.md).

Before writing report HTML, resolve the track through `scripts/report_template.py`. Inspect the maintained implementation and its pinned example. Reuse that implementation and its release checks; a different cohort or diagnostic evidence state calls for a data adapter or explicit mode. If no track fits, create the smallest reusable renderer with a synthetic runnable example and behavioral checks, then register it in `configs/report-templates.json`. Improve the owning template and this skill from demonstrated gaps, without embedding run-specific prose or copying old measurements. A report must carry the selected template identity and input hashes so its release check can detect a bypass.

Derive bilingual counts, budgets and method labels from the selected inputs.
Bind cohort-specific access and interface documentation in the participant registry;
reuse engine-level adapter contracts instead of adding per-version renderer tables.
Keep report-specific prose and its translations in explicitly bound content;
do not choose a historical article from provider names or task-family guesses.
Without a content binding, render the maintained data-derived explanation.
Prove reuse through the public build and check entries with a different external
task/method inventory, not only by searching for old numbers in source code.
Exercise assembly and packaging with that external profile as well. Participant
IDs, labels, interfaces and comparison roles come from the instance registry;
a new cohort must not inherit a historical method's account or timing claims.
Disclose declared literal, fact, body-region, record, resource and identity checks
separately. A task instruction is not a full-text reference. Preserve frozen
verdicts while exposing passes without verified page identity, blocked-page
markers and unexpected resource URLs as diagnostics.

An explicitly requested diagnostic report may be delivered separately from a certified benchmark release. Use the diagnostic build/check entry, label its evidence state and certification gaps on every page, preserve saved scores and outputs, and do not publish rankings or upgrade certification. It does not waive any certified-release reference or review gate. Presentation fixes add no provider calls. Update only the owning code, relevant instructions and demonstrated regression scenarios; do not mechanically modify every skill or copy report-specific conclusions into instructions.

## Working contract

Public report CLIs fall back to English. `BROWSER_EVAL_REPORT_LANGUAGE` changes the environment default; an explicit `--language` wins, and a rebuild preserves the saved primary language. For an agent-driven build, select the language from the current conversation. English builds retain the matching Chinese edition; a Chinese selection does not generate an English translation unless explicitly requested. Keep measured material in its source language. Historical Chinese-only renderers must disclose that limit and reject unsupported requests. Bind generated languages and the selected entry in report metadata. English navigation opens a not-generated notice when absent; never publish stale English pages. Validate each generated edition and retain route, value and evidence-link parity checks when both exist.

1. Establish the decision being supported, the measured unit, cohort, versions, execution location, freshness policy, and intended deliverable. Inspect the actual dataset, runner, outputs, scorer, prior review evidence, and current report. Do not infer them from screenshots or prose.
2. Freeze a machine-readable evaluation contract before expensive calls. It must identify the exact cases and methods, run count, timeout, concurrency policy, input and output contract, scoring provenance, reference state, and publication target when one exists. Existing compiled reference answers are frozen by default. Source metadata, capture dates, and absent newer acceptance-contract fields do not imply a live-reference task. Only tasks explicitly declaring `source_verification.reference_mode: live_exact` require live checks. For those tasks, fail closed: verify and freeze references immediately before calls, verify the same declared scope afterward, and certify the cohort only when both checks pass. An unchanged compiled acceptance-contract dataset is already the benchmark input: freeze its task and rule bytes for execution, without requiring historical source-qualification raw files or re-auditing cohort admission. Qualify sources when adding tasks or changing references/acceptance rules; do not turn an ordinary version rerun into dataset reconstruction. Partial or uncertified cohorts remain diagnostic evidence and cannot enter a release.
3. Let each method use its strongest documented route to produce the required output. Equalize the task, inputs, output contract, call budget, and scoring. Do not require identical internals. For fixed-native cohorts, declare the input media type independently of URL patterns and expected answers, freeze the class-level route and transport parameters before calls, and verify the retained request receipts at aggregation and release. Record material asymmetries such as local versus remote execution, cold versus warm start, bundled conversion, network transit, account tier, and billable time.
4. Preserve evidence as an append-only chain by default: task definition, reference, provider call, raw artifact, normalized artifact, score, review, revision, and release. If the user explicitly requests deletion of superseded history, validate the chain first and use the existing owner's current-only authority described in [audit and revision](references/audit-and-revision.md); remove obsolete full snapshots rather than storing reverse patches. Keep current provider outputs, call identities, real scoring sources and necessary review evidence immutable. Routine refresh and publication reuse current inputs without redundant historical snapshots. A presentation or scoring change must not masquerade as a new provider result. Declare the native answer fields and validate their structural projection before accepting a scored artifact. Adapters must not remove returned candidates by URL meaning or consult expected answers when selecting candidate fields. Apply privacy redaction only to separately identified public views after scoring, preserving the private originals and distinguishing original, scored and public-view hashes.
5. Separate retrieval success, task acceptance, content quality, latency, cost, and freshness. Treat missing telemetry as unavailable. A request that received no first byte contributes to total latency, but not to first-byte or body-transfer stage means; show each stage’s observed population. A missing or partial reference makes the affected metric unavailable or scoped; it does not prove success or failure. Do not derive binary acceptance from an unrelated aggregate similarity score.
6. For heterogeneous same-window latency evidence, preserve the full predeclared task-by-method matrix. Show all-call latency on that common denominator separately from successful-call latency on each method's conditional subset. Treat the all-method shared-pass intersection as a diagnostic, never as a post-outcome selector or ranking sample.
7. At aggregate and publication boundaries, copy only explicit hash-bound evidence and public audit files. Reject symlinks and exclude worker trees, host paths, credentials, and internal run logs. Verify primary, auxiliary, delivered-file, and retained retry artifacts before release.
   When a saved-input handoff needs host-metadata redaction, use the shared publication privacy owner. Preserve private originals and distinguish original from public file hashes; rebind dependent metadata references and disclose auxiliary debug-log projections. Never rewrite original scored answers, references or response payloads as a privacy repair. When a scored answer or native envelope itself contains private values, create a separately identified post-score public view through the shared privacy owner, verify its native-field projection and unchanged task verdict, and disclose that omitted original bytes cannot be reconstructed from the public copy. A public frozen presentation must explicitly bind its complete view inventory, verify displayed bytes against those views, keep acquisition gates strict, and distinguish preserved original diagnostics from what redacted text can reproduce. Verify unchanged verdicts, timings and review decisions, and rebuild from the public inputs. Keep regenerated package/report wrappers outside the original-input projection receipt so normal rebuilding does not invalidate it or create circular hashes.
8. Independent result review is opt-in: do not start it unless the user explicitly requests review. When requested without a broader scope, review machine failures only; review machine passes or every attempt only when the user explicitly asks for full review. Label a review performed with known machine outcomes as an evidence audit, separately from independent blind-review coverage; a fresh review-origin tag alone does not establish blindness. Keep the initial verdict blind, but do not equate agreement with correctness: run the anomaly investigation in [audit and revision](references/audit-and-revision.md) for triggered results before calling the review complete. For a large independent review that is safely batchable, prefer resumable Codex CLI jobs with an exact model ID, read-only sandbox, strict output schema, non-overlapping packets, and bounded concurrency; persist each completed packet so a retry never repeats substantive review. Record the exact reviewed/total coverage, accepted unreviewed passes, reviewer, disagreements, adjudications, anomalies, and unresolved items. A review summary never replaces per-attempt evidence.
For an explicitly requested full review, set `independent_result_review_scope: all` in the report profile and carry it into the assembled dataset comparison. The release boundary must reject failure-only review, missing attempts and accepted unreviewed passes. Reuse an existing content judgment only when the raw answer and acceptance contract are unchanged; verify each new call identity separately. For failed calls, inspect the current runtime evidence even when the answer is unchanged or empty: the same empty output can hide different failures. Changed evidence needs supplemental review, not another review of the unchanged population.

9. Rerun only calls whose inputs, provider route/version, runtime contract, or source validity changed. Recompute downstream scores and reports without provider calls when only scoring, annotations, or presentation changed. Never retry merely to obtain a better result. A reference or verdict correction must identify the shared root cause and add executable positive/negative controls through the production scorer; bind those regression IDs in the revision receipt instead of patching stored verdicts.
10. Build the public report from the frozen evidence. Every page should answer a reader question. Show task and run identifiers, the exact expected-versus-actual evidence behind a verdict, denominators and unavailable metrics, method differences, limits, concise reproduction instructions, and a compact review history.
11. Release only after code tests, evidence integrity, report links, deterministic interaction checks, the one-pass top-level-page reader review, secret scans, target-site checks, and deployed-output verification pass. Responsive and theme matrices belong to shared renderer tests when those owners change; do not replay them for every report release. Preserve earlier sites unless replacement was explicitly requested.
12. Present one authoritative logical result per method and task. The frozen protocol chooses the score-bearing execution; a renderer must never silently replace that rule. Keep every physical call in the evidence chain retain accumulated execution cost separately from the declared primary latency metric. State whether primary latency measures the final call or the complete user-visible operation; retries never increase the logical-result denominator. Do not make execution ordinal labels part of the public comparison unless retry behavior is the declared subject.
13. Apply the track-independent presentation contract in [report and release](references/report-and-release.md) to every report, including a new track. Wire semantic checks into its release command; prose instructions alone are not a release gate.
14. In this repository, publish a finished report to the Artifact Site host and site bound by that track’s current profile, following that host’s current `for-agents.md`. Never embed a default deployment hostname or borrow a target from another report. Build and validate before the external write, bind the returned slug, share URL, and version in the current profile, use optimistic locking for later updates, and verify the unauthenticated share page. Use repository publish targets when available; do not reconstruct a target from prose or an earlier receipt. Export the whole deployed tree only when the specific report publisher requires it or when diagnosing a release failure.
15. Make summary and interpretation routes presentation-ready. Give each section one finding and one primary visual such as a chart, mechanism diagram, evidence specimen, or meaningful image, with short supporting prose and exact values in accessible text. Do not add decorative hero art. Keep sortable inventories, logs, and full evidence on their functional pages. For Chinese report-authored prose, run both the repository Humanizer and Humanizer Chinese after the evidence pass; preserve measured language and quoted evidence.

## Stop conditions

Do not publish while the exact method matrix is incomplete, references required for the claimed metrics are unverified, an explicitly requested review scope is incomplete, unresolved adjudications remain, output identities cannot be reproduced, or the publication target is ambiguous. Report unreviewed scope honestly; do not imply that failure-only review covered machine passes.

When finished, give the report and code-review links, measured and reviewed coverage, unresolved count, rerun scope, material limitations, and verification status. Keep internal notes and agent transcripts out of the public report.

For a new URL-only local matrix over committed stable contracts, reuse the local-audit renderer with comparison.kind=uniform_native_local_measurement and a pre-call local-run-contract.json. Require complete case-by-method coverage and one hash-bound public call log per result. This mode must state that results are machine judgments without independent review and must never inherit historical review receipts or certification.

For an explicitly requested share of an existing frozen local snapshot, pass its
`--presentation-binding` to the maintained package builder and package checker.
This verifies the saved evidence without upgrading certification; the ordinary
publication path keeps its certified-evidence gate.

For an oversized saved report, use the maintained packer's content-defined
compression and `--cached-content` support to reuse completed public projections.
Preserve all source/file hashes and run its complete publication check; do not
drop evidence or create a separate renderer to satisfy upload limits. Reuse must
refresh derived result privacy/footprint projections from the same bound source;
a cached representation passing a secret scan alone does not prove it matches
the current export contract. Frozen public snapshots can retain
original scoring hashes alongside separately bound public-view bytes. Reuse the
existing public-view verifier and preserve both identities; a public view must
never be described as the original scored bytes.

When a complete compressed report exceeds one artifact's capacity, prepare
capacity-bound data attachments through the existing packer. Check their combined
evidence before uploading, bind immutable published versions, and include the
attachment archives and target registry in the main publication snapshot. Keep
one report entry and all evidence; reject unresolved or changed data attachments.

Web Search relevance labels cover every in-house case with the same frozen rubric.
Use named human annotations by default. When the user explicitly selects a model
judge, use `scripts/judge_search_relevance.py` and retain the exact model, prompt,
blind inputs, raw responses and quoted scoring evidence. Label the result as model
preview relevance rather than human gold judgments. Both routes import through
`--relevance`; neither may substitute nonempty responses or URL checks for labels.
Keep empty queries and all-zero pools in the score population.
For explicit website or official-source requirements, bind independently
qualified URL rules and check eligibility before model grading. Ordinary
diagnostic domains must not become mandatory sources. Keep source URLs out of
content-only judging, retain original model grades, and bind rubric corrections
separately from immutable search calls.
Register explicit official-source and platform/genre requirements in every query
language; organization names used only as diagnostics do not impose a source
restriction. Qualify valid official alternative domains independently. When
source rules change, reuse original judge calls and regrade only cases whose
actual judge inputs changed.

When published references contain terminal footnotes or fused citation URLs,
separate that metadata only in a versioned answer derivation. Preserve the
original answer, keep answers that are themselves URLs intact, and bind paired
public-scorer/task-predicate controls before any offline recomputation.

Make hash-bound original query sources reachable from the dataset page.
A provenance hash without an inspectable source does not support a reader’s
source check. Keep the source author’s historical results separate from current
benchmark measurements.

Review packets must use the same adapter, request-bound URL resolution and
organic-slot projection as the scored response, and reject a projection/hash
mismatch. Independent factual verification needs live read-only source access;
retain physical review inputs and outputs privately and publish path-free
provenance projections with distinct original and public hashes. A rate-limited
review stops new jobs and resumes only missing packets.

For native SERP adapters, retain the complete response but score the declared
organic slots in their original order. Test wrapped JSON, absent optional
snippets, relative links and invalid entries inside versus outside the cutoff.
For new search cohorts, finish one provider's complete case set before starting
the next provider; never interleave providers by query. Continuing a frozen
cohort preserves its existing execution order.
A shared projection correction uses the versioned assessment revision over
hash-bound saved responses, with paired controls and zero new search calls.
Before finite-quota collection, reserve configuration probes and validate the
complete matrix's worst-case physical call count; resuming must count retained
calls as well as current logical results.
When a remote native interface changes during collection, stop further calls,
prove the supported replacement request, and bind an execution revision before
resuming. Recollect the declared changed-method scope while preserving complete
unaffected calls. Keep archived physical calls in quota accounting, including
when a second interruption occurs during the correction.


For Search comparison reports, keep decision summaries distinct from complete result
and audit inventories. A subset discovery claim must show every selected provider
on that same subset; the headline relevance score covers the full declared relevance
population. Give quality, collection timing/billing, input provenance and original
responses distinct owners, and remove duplicate reading routes when consolidating them.

Search builds and scoring reuse validate evidence without rechecking report HTML.
Combine the final `--release-check` and `--export-delivery` into one pass. Subsequent
packaging and upload verify the accepted file tree instead of repeating page checks.

Conciseness must preserve the evidence needed for the decision. Keep each independent
benchmark’s primary quality measures and denominator visible in the home comparison;
do not replace them with a narrow diagnostic or hide them inside disclosure controls.
Reorganization must account for each substantive finding, calculation and evidence
entry before retiring a section.
