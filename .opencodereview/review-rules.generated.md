<!-- Generated from docs/references/review-guidelines.md and .opencodereview/review-rules.md; edit those inputs. -->

# Review Guidelines

The canonical version of this document lives in `lexmount/ai-template-py` at
`docs/references/review-guidelines.md`. Consumer repositories synchronize their
copies with `scripts/review_guidelines.py` and pin the source revision in
`review-guidelines.lock.json`. Change the canonical document rather than editing
a synchronized copy.

OCR's official rules remain the foundation. This document adds shared review,
scope, and feedback requirements. Read the repository's local review supplement
for its domain requirements. Use the review language required by the user or
repository; preserve code identifiers, commands, schema values, and product names.

## Establish the review basis

Read the task goal, PR description, linked issues and plans, user agreements,
acceptance criteria, and available validation results. Read all top-level
conversation comments, formal review bodies, inline threads, and replies. Earlier
reviews provide evidence and the status of earlier findings; they do not replace
an independent assessment of the current candidate.

Confirm the target branch and current head. Read the complete net diff from their
actual merge base to head, together with all unmerged commits. A `synchronize`
event or triggering commit starts a review; it does not redefine the PR as only
that increment. Inspect every changed file assigned by the tool, using other
files to establish impact. A complete PR review must cover the full net diff, not
infer scope from the title. Correct a stale checkpoint before reviewing; if the
correct diff is unavailable, report the input problem rather than attributing
existing target-branch changes to this PR.

Follow the repository entry points to the architecture, interface, code-style,
security, and deployment guidance relevant to the affected components. Read local
supplements and linked component documents where applicable. Concrete product
constraints belong in those sources rather than a shared product checklist.
Consult existing code, callers, history, and upstream sources to establish impact.
Reading more code does not expand this PR's responsibility for fixing it.

## Decide whether a finding belongs to this PR

Each finding must meet both conditions:

1. Identify the specific change that introduces, triggers, or worsens the problem,
   or the agreed task requirement that remains unimplemented. A new path can
   trigger a defect in old code. Merely finding a defect in the same file or call
   chain does not make it part of this PR.
2. Ground the finding in a code path, reproduction, test, or explicit contract.
   For behavior defects, explain a reachable trigger and its consequences,
   including error paths and untrusted inputs, and account for existing validation
   and caller constraints. For a mandatory-rule violation, cite the applicable
   rule and the changed location that violates it. Do not present an unproven
   runtime failure as its consequence.

Keep proposed fixes proportionate to the problem and preserve correct behavior.
Do not attach surrounding cleanup, a global redesign, or new product requirements.
A confirmed defect remains reportable when its exact fix is not yet known.

Mandatory official and repository rules still apply to violations introduced or
worsened by the change. Preferences such as "prefer" or "where practical" allow
contextual choices; a different preference is not itself a defect. Explain the
conflicting rules, responsibilities, resource ownership, or maintenance cost
behind a design finding. File length or a tidier alternative alone does not
justify a rewrite.

Keep independent historical defects out of this PR's fix list; identify their
separate ownership when follow-up is needed. A material validation gap must name
the affected scenario and the evidence needed. Missing command output alone does
not establish a code defect. Investigate a suspected regression against its
history and current reachable paths before reporting it as confirmed.

## Required checks

Apply these questions to the current changes and affected behavior. Use the
evidence and ownership conditions above when reporting findings or requesting fixes.

1. **Goal and approach.** What real problem does the PR solve, which user scenarios
   does it affect, and what behavior should change or remain unchanged? Does the
   description match the existing code? Is the approach necessary, and does it
   implement the whole goal?
2. **Acceptance criteria.** Are all success criteria covered? Has a review with no
   findings been mistaken for verification that the requirements are met?
3. **Repository requirements.** Does the change follow the repository entry points,
   project overview, architecture sources, and language and code-style guidance?
4. **Architecture boundaries.** Does the change preserve dependency direction,
   module responsibilities, data flow, and ownership? Trace the actual producers
   and consumers through callers, state, branches, configuration, caches,
   asynchronous work, and errors. Where identities, sessions, tasks, or resources
   are involved, do concurrency, cancellation, and cleanup respect their ownership
   and permission boundaries?
5. **Simplicity.** Is this the simplest implementation that meets the goal? Does it
   add unnecessary state, configuration, abstractions, or dependencies?
6. **Regression and compatibility.** Along affected call paths, do existing correct
   features, default configurations, old data, and recovery scenarios still work?
   Does the change introduce a bug or break an existing agreement? When replacing
   an entry point, removing code, or changing an interface, test the replacement
   against the original supported scenarios, not just whether the new entry point
   runs. An intentional behavior change needs an explicit requirement and any
   necessary migration arrangements.
7. **Reuse and maintainability.** Are the responsibilities, names, and locations of
   modules, interfaces, classes, and functions clear? Are files becoming too large
   to keep those responsibilities clear? Who owns state and resources, and is the
   same rule implemented in several places? Prefer existing shared capabilities;
   logic used by several real callers belongs at a suitable shared boundary rather
   than in a PR-specific copy. Another instance of an already supported kind should
   extend its existing owner through data, configuration, or a registry when that
   contract supports it, rather than require scattered instance-specific branches.
   Abstractions should express stable responsibilities and simplify callers and
   tests. Use classes and composition when state, lifecycle, or a replaceable
   protocol needs encapsulation; keep simple stateless logic as functions. Do not
   require object-oriented design, inheritance, or a framework for hypothetical
   needs. A design finding must identify a concrete maintenance cost or error risk,
   the responsibility or constraint to restore, and the smallest fix when known.
   Personal style alone is not grounds for rewriting code.
8. **Necessity of each file.** Is every assigned changed file relevant and necessary
   to the goal? Flag scope creep and unrelated changes to another owner's logic.
   Explain how each defect was introduced or triggered by this PR, including
   regressions caused by its fixes. Inspect affected shared paths and similar
   instances of a confirmed defect so the fix covers the same problem within the
   affected scope. Search for duplicated rules, obsolete concepts, adjacent
   implementations, and conflicting sources of truth as needed. Independent old
   defects that the change does not trigger remain separate work.
9. **Robustness and error handling.** Does error handling follow the repository's
   fail-fast requirements and the recovery behavior allowed by the product? Are
   errors swallowed, required inputs hidden by defaults, or mandatory steps
   bypassed? Can failures be traced to their cause? For external writes, files, and
   resources, examine partial success, timeouts after an operation has taken
   effect, retries, interruption, rollback, and cleanup together. Error handling
   must not cause duplicate operations, lose original data or committed results,
   or delete resources needed for recovery.
10. **Validation coverage.** Do validation commands match the change's risks? Tests
    should follow user scenarios and behavior contracts, covering existing correct
    behavior, new behavior, accepted and rejected cases, and relevant failures
    rather than repeating the implementation. Do tests run in the default
    collection? Do mocks hide real interface changes, or results depend on local
    machine configuration? Do regression cases run by default? Use real
    producer/consumer contracts. Inspect commands and results for the reviewed
    revision; when permitted, run focused reproductions and passing controls.
    When packaging or distribution is affected, verify the installed package as
    well. Type, format, syntax, and other static checks do not establish runtime
    correctness. Choose verification appropriate to the affected behavior; do not
    add redundant tests or demand unrelated end-to-end runs for documentation-only
    changes.
11. **Historical regressions.** When migrating, rewriting, or restoring old code,
    defaults, delays, fallbacks, special cases, or test semantics, ask: "Did this
    behavior exist before? Was it removed because it caused a problem? Does that
    reason still apply?" Consult Git history when needed. For a suspected revival,
    check the current reachable paths, the reason for the original fix, and the
    validation evidence. Report a confirmed return of broken behavior; do not
    present suspicion as an established defect.
12. **Upstream capabilities.** For framework or provider behavior, have the locked
    version and current official documentation, APIs, and release notes been
    checked, with upstream source consulted where needed? Does the change use
    official lifecycle hooks and extension points where suitable? If a custom
    adapter works around an upstream limitation, is the gap reproducible, is the
    workaround confined to the protocol boundary, and does it have behavior tests?
13. **Human approval boundaries.** Do tool permissions, user authorization, and the
    actual operations agree? High-risk operations must respect required human
    approval; unrelated approval steps should not repeatedly interrupt reversible,
    low-risk actions.

`review` assesses the implementation and change risks. `verify` determines whether
the agreed success criteria have been met. A review with no findings does not
replace necessary acceptance checks.

## Priority levels

First establish that the finding is valid and belongs to this PR. Then classify
its actual impact and urgency:

| Level | Criteria |
| --- | --- |
| P0: urgent, critical | Widespread loss of core service, major irrecoverable data loss, or a severe breach of security boundaries requiring immediate action. A security or data concern is not automatically P0. |
| P1: high priority | Broken primary functionality, a critical user journey, or a compatibility contract; or substantial security, data, or resource risk. The impact falls short of P0 but warrants priority repair. |
| P2: normal priority | Lower or localized impact, with evidence of a functional, boundary, maintainability, validation, or applicable mandatory-rule defect. Personal preference is still not a defect. |

Label each finding P0, P1, or P2 and explain the classification. Preserve the tool's
field protocol: if it only provides `severity`, use `critical` for P0, `high` for
P1, and `medium` or `low` for P2 according to impact, with the P level in the
comment text. Severity and blocking status are separate judgments. All three
levels are blocking by default under this shared policy; exceptions require an
explicit agreement.

## Output and feedback handling

Follow the review tool's result structure and the priority definitions above.
Report all valid findings within scope, sorted by severity, without a fixed count
limit. Combine findings with the same root cause. Place inline comments at changed
locations allowed by the tool; old code may still supply root-cause evidence. Each
finding should state its relationship to this PR, evidence or applicable rule,
impact, and the behavior or constraint to restore. Give the smallest correction
when it is known.

Valid in-scope findings, including P2 and findings from either official or local
rules, block merging by default. Mark a finding non-blocking only when the user
or repository maintainer has explicitly agreed to that exception, and cite the
agreement.

Apply the same judgment to feedback on the PR. Fix valid findings owned by this
PR, check the current behavior for already-fixed findings, explain why rejected
findings do not hold, and leave unrelated old defects to their own tasks. Read
replies as well as thread roots. A valid finding does not make its suggested fix
correct: do not delete correct test expectations or change agreed requirements
merely to eliminate a comment.

Keep summaries and execution records out of inline findings. The rules do not
themselves authorize a model to publish comments. With no findings, return the
empty result required by the tool protocol. When a summary is requested, lead with
"pass", "conditional pass", or "fail" in the required review language, then state
the covered paths, evidence-backed residual risks, and unverified items. Do not
invent risks or repeat the checklist. Use full URLs or `owner/repo#number` for
cross-repository references; verify the repository, title, relevant revision, and
merge state. Distinguish a tested pair of revisions, an unmerged dependency, and
a defect in the current candidate.

## Re-review and completion

The first pass covers the assigned net diff and reports confirmed findings
promptly, without saving some for later rounds. Use independent or parallel
reviewers for distinct risks when the host supports them, authorization permits
it, and they improve coverage. Re-review may focus on unresolved
findings, fixes, and their effects, while retaining the same task goal and full
PR scope. Follow any explicit requirement for a fresh review of the whole current
candidate. Previous approvals or findings do not substitute for checking the
current changes.

Missed findings may be added with newly confirmed evidence and a clear
relationship to this PR. That evidence may come from code unchanged since the
previous review; a new commit is not required. Reopening a handled finding needs
evidence that the defect remains or that its fix caused a regression.

Touching more files during a fix does not require cleaning up every old problem
in those files. The work can close when valid in-scope findings are resolved and
necessary verification passes. Add task requirements only when the user or
repository maintainer explicitly changes the goal.


---

# Browser Eval review supplement

Follow the repository review constraints in `AGENTS.md` and `CONTRIBUTING.md`. The following requirements belong to Browser Eval and supplement the shared criteria in `docs/references/review-guidelines.md` and OCR's built-in rules.

## Repository checks

Keep provider execution and reporting in the harness; use benchmark-owned definitions and scoring. Trace scored answers to native evidence. Verify populations, timing and certification against the selected track contract. Preserve frozen evidence and report targets, keep private run data outside Git, and check report views agree with their evidence.
