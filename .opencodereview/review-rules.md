# Repository review supplement

These requirements supplement OCR's built-in instructions. Keep its severity thresholds, output format and execution policy. Follow the review constraints in `AGENTS.md` and `CONTRIBUTING.md`.

## 1. Does the PR achieve its purpose?

Read the goal and acceptance criteria, then trace the implementation through its callers. Verify the proposed solution and each changed file serve that goal.

## 2. Could it break existing behavior?

Check working scenarios, compatibility and failure paths, including retries, cancellation and concurrent work where relevant. Investigate the shared cause of defects; consult history when restoring previously removed behavior.

## 3. Is the design maintainable and reusable?

Require sound object-oriented design: clear responsibilities, encapsulated state and stable interfaces. Reuse existing components and keep shared logic under one owner. Keep simple stateless functions simple. Avoid unnecessary abstractions and report-specific branches; another supported report or provider should use data or configuration rather than scattered code changes.

## 4. Does the evidence prove it works?

Check behavior tests against the requirements, including valid and invalid scenarios and regression controls. When execution is available and permitted, run focused checks on the reviewed revision. Use real producer/consumer contracts and test the installed package when affected.

## 5. Has the whole change been reviewed?

Inspect all changed files, affected shared paths and similar occurrences. Use parallel agents for distinct risks when supported by OCR. Recheck earlier findings on the current revision, combine duplicates and report actionable defects with a concrete scenario and code evidence. Treat design concerns as findings when they have a demonstrable consequence.

## Repository checks

Keep provider execution and reporting in the harness; use benchmark-owned definitions and scoring. Trace scored answers to native evidence. Verify populations, timing and certification against the selected track contract. Preserve frozen evidence and report targets, keep private run data outside Git, and check report views agree with their evidence.
