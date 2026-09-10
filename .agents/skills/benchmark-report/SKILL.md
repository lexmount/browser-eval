---
name: benchmark-report
description: Design, run, audit, revise, and release evidence-backed benchmark reports that compare systems, providers, models, tools, or versions. Use for benchmark methodology, scoring, result dashboards, reproducibility, independent result review, or publication. Not for a standalone editorial article without benchmark maintenance.
---

# Benchmark report

Produce a report whose conclusions can be traced to frozen inputs, measured outputs, scoring rules, and an explicit result review. Optimize for a reader deciding what the benchmark means, not for preserving the author's workflow.

Read the repository's `AGENTS.md` first. Project rules override this general skill. Before changing a benchmark, read [evaluation design](references/evaluation-design.md). Before reviewing scores or deciding whether to rerun, read [audit and revision](references/audit-and-revision.md). Before building or publishing a report, read [report and release](references/report-and-release.md).

## Working contract

1. Establish the decision being supported, the measured unit, cohort, versions, execution location, freshness policy, and intended deliverable. Inspect the actual dataset, runner, outputs, scorer, prior review evidence, and current report. Do not infer them from screenshots or prose.
2. Freeze a machine-readable evaluation contract before expensive calls. It must identify the exact cases and methods, run count, timeout, concurrency policy, input and output contract, scoring provenance, reference state, and publication target when one exists.
3. Let each method use its strongest documented route to produce the required output. Equalize the task, inputs, output contract, call budget, and scoring. Do not require identical internals. Record material asymmetries such as local versus remote execution, cold versus warm start, bundled conversion, network transit, account tier, and billable time.
4. Preserve evidence as an append-only chain: task definition, reference, provider call, raw artifact, normalized artifact, score, review, revision, and release. Keep provider output and call identity immutable. A presentation or scoring change must not masquerade as a new provider result.
5. Separate retrieval success, task acceptance, content quality, latency, cost, and freshness. A missing or partial reference makes the affected metric unavailable or scoped; it does not prove success or failure. Do not derive binary acceptance from an unrelated aggregate similarity score.
6. Review results as a first-class stage. Inspect all attempts, not only machine failures, using the task contract, reference, and complete actual output. Record coverage, reviewer, disagreements, adjudications, and unresolved items. A review summary never replaces per-attempt evidence.
7. Rerun only calls whose inputs, provider route/version, runtime contract, or source validity changed. Recompute downstream scores and reports without provider calls when only scoring, annotations, or presentation changed. Never retry merely to obtain a better result.
8. Build the public report from the frozen evidence. Every page should answer a reader question. Show task and run identifiers, the exact expected-versus-actual evidence behind a verdict, denominators and unavailable metrics, method differences, limits, concise reproduction instructions, and a compact review history.
9. Release only after code tests, evidence integrity, report links, responsive/visual checks, secret scans, target-site checks, and deployed-output verification pass. Preserve earlier sites unless replacement was explicitly requested.
10. Present one authoritative logical result per method and task. If the frozen protocol permits one additional execution after failure, keep physical calls in the evidence chain, use the final execution without best-of selection, and aggregate all latency and cost. Do not make execution ordinal labels part of the public comparison unless retry behavior is the declared subject.
11. Treat visual QA as evidence QA. Inspect every core route class at desktop and narrow widths in light and dark themes. Reject one-character vertical table wrapping, unreadable code/evidence contrast, clipped controls, text-heavy raster diagrams, and mobile layouts that merely shrink dense content below readable size.

## Stop conditions

Do not publish while the exact method matrix is incomplete, references required for the claimed metrics are unverified, review coverage is incomplete, unresolved adjudications remain, output identities cannot be reproduced, or the publication target is ambiguous. Report the narrow blocker; do not silently weaken the benchmark.

When finished, give the report and code-review links, measured and reviewed coverage, unresolved count, rerun scope, material limitations, and verification status. Keep internal notes and agent transcripts out of the public report.
