# Repository review supplement

These requirements supplement OCR's built-in instructions. Keep its severity thresholds, output format and execution policy. Follow repository review constraints in `AGENTS.md` and `CONTRIBUTING.md`.

## 1. Does the change meet its goal and constraints?

- Read the PR, linked requirements, relevant comments, architecture and code-style guidance before judging the diff.
- Trace runtime behavior through callers; check the approach and every changed file against the acceptance criteria. Finding no defects alone does not verify completion.

## 2. Could it break correct behavior?

- Check existing callers, inputs, data and configuration, including necessary migration paths and relevant timeout, retry, cancellation and concurrency scenarios.
- Look for swallowed errors, defaults hiding required inputs and bypassed checks. When restoring old behavior or changing defaults and fallbacks, check why the previous behavior changed and whether that reason still applies.

## 3. Is the design sound and reusable?

- Require sound object-oriented design: clear responsibilities, encapsulated state and stable interfaces. Reuse existing components; keep shared behavior under one owner.
- Check naming, file placement and dependency direction. Choose the simplest maintainable solution; avoid unnecessary state, configuration, dependencies and class hierarchies.
- Keep simple stateless functions simple. Another supported report or provider should use data or configuration, without report-specific branches or scattered code edits.

## 4. Are upstream and cross-repository dependencies handled correctly?

- Check locked versions against official APIs and lifecycle hooks. Custom adapters need a reproducible upstream gap, a clear protocol boundary and behavior tests.
- Verify related PR revisions and merge state; use full URLs. Distinguish a working paired version, an unmerged dependency and a current defect.

## 5. Does validation cover the actual behavior?

- Check requirement-based tests for accepted/rejected scenarios and regressions, using real producer/consumer contracts. Static checks alone do not establish runtime correctness.
- Inspect validation commands and results for the reviewed revision. When permitted, run focused reproductions and passing controls, including installed-package checks when affected. Identify material validation gaps.

## 6. Has the whole change been reviewed?

- Inspect every changed file, affected shared paths and similar defects. Use parallel agents for distinct risks when supported by OCR; finish the affected scope before reporting.
- Recheck earlier findings, verify suspected causes and combine duplicates. Report actionable issues with their trigger, consequence, code location and suggested correction under OCR's reporting rules.

## Repository checks

Keep provider execution and reporting in the harness; use benchmark-owned definitions and scoring. Trace scored answers to native evidence. Verify populations, timing and certification against the selected track contract. Preserve frozen evidence and report targets, keep private run data outside Git, and check report views agree with their evidence.
