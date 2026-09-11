# Audit and revision

## Evidence chain

Retain enough information to recompute and explain every public result:

```text
case + reference + method/runtime
              ↓
single call identity + raw artifact
              ↓
normalization + score + verdict
              ↓
independent review + adjudication
              ↓
release manifest + deployed bytes
```

Hash and bind transitions where accidental mixing is plausible. Validate exact cardinality and uniqueness, not only totals. Treat raw outputs, timestamps, request status, method version, and call IDs as immutable measured evidence.

Source-dependent calls require a fail-closed boundary: capture and freeze the declared reference scope immediately before execution, repeat the independent observation after execution, and bind both checks to the complete cohort. A process exit, partial attempt count, changed reference, or missing post-check makes the cohort diagnostic rather than publishable; never salvage its successful rows into the release matrix.

## Independent result review

Do not start independent result review automatically. It is an expensive, user-directed stage. When the user requests review without saying full/all, review only machine failures; include machine passes only after an explicit full-review request. Whenever practical, hide the machine verdict and score until the independent judgment is recorded, and supply the complete answer-bearing evidence (including allowlisted source title/resource metadata rather than machine-derived candidates).

The review record should contain reviewer identity or class, protocol, exact attempt inventory, total matrix size, accepted unreviewed machine passes, input hashes, independent verdict, disagreement reason, adjudicated verdict, and unresolved state. Public presentation must state the reviewed fraction and must not label failure-only review as all-result review.

Formatting differences are not failures when the output contract treats them as equivalent. Missing boundaries, encoding corruption, challenge pages, wrong artifacts, stale content, and incomplete structures are substantive when they violate the contract. Do not create provider-specific scoring exceptions to repair a general normalization problem.

## Decide what to rerun

Use dependency impact, not convenience:

| Change | Provider call needed? |
| --- | --- |
| CSS, navigation, wording, report layout | No |
| Corrected generic normalization or scoring | No; rescore and re-review affected judgments |
| Added independent annotation without changing source/input | No; rescore affected cases |
| Provider version, command, API route, credentials tier, timeout, or runtime behavior | Yes, affected method/cases |
| Task URL, required output, source bytes, or acceptance contract | Yes, affected cases/methods unless the change is explicitly a score-only correction |
| Broken or volatile reference replaced | Usually only the affected cases, followed by exact re-review |

Never overwrite old evidence or select a later response because it scores better. Assemble a new complete evidence view atomically, record inherited versus rerun attempts, and verify all downstream bindings.

## Revision receipts

A score-only revision must record old and new dataset/result hashes, changed fields, decision changes, provider call count of zero, reviewer binding, and predecessor history hash. A new provider call must receive a new call identity and must not be described as a scoring correction.

Do not treat changed reference text or a list of flipped verdicts as a completed correction. Record the defect mechanism, not only its symptom, and bind each revision to compact synthetic regression cases that execute the same public scorer and task predicate as live evaluation. Include both a valid equivalent form and a nearby invalid form whenever the defect can create errors in both directions. Validate this provenance before writing the revised dataset or results; missing or unknown regression IDs fail closed.
