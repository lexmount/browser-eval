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

Live exact-reference calls require a fail-closed boundary: capture and freeze the declared reference scope immediately before execution, repeat the independent observation after execution, and bind both checks to the complete cohort. A process exit, partial attempt count, changed reference, or missing post-check makes the cohort diagnostic rather than publishable; never salvage its successful rows into the release matrix.

## Independent result review

Do not start independent result review automatically. It is an expensive, user-directed stage. When the user requests review without saying full/all, review only machine failures; include machine passes only after an explicit full-review request. Whenever practical, hide the machine verdict and score until the independent judgment is recorded, and supply the complete answer-bearing evidence (including allowlisted source title/resource metadata rather than machine-derived candidates).

The review record should contain reviewer identity or class, protocol, exact attempt inventory, total matrix size, accepted unreviewed machine passes, input hashes, independent verdict, disagreement reason, adjudicated verdict, and unresolved state. Public presentation must state the reviewed fraction and must not label failure-only review as all-result review.

Formatting differences are not failures when the output contract treats them as equivalent. Missing boundaries, encoding corruption, challenge pages, wrong artifacts, stale content, and incomplete structures are substantive when they violate the contract. Do not create provider-specific scoring exceptions to repair a general normalization problem.

Agreement between the reviewer and scorer proves only that the saved answer fits the current contract; it does not prove that the contract or evaluation path is sound. After recording the blind verdict, deterministically flag suspicious results and investigate the complete chain: task input, frozen reference, provider request contract and runtime, raw response, adapter transformation, scorer, and final verdict. At minimum flag successful requests with empty answers, nonempty raw responses that become empty or implausibly small, identical short answers across unrelated URLs, tool failures that occur before meaningful network work, results that satisfy the positive structure but fail only on excluded content, provider-by-family zeroes or sharp discontinuities, and anomalies raised by the reviewer. This second stage may inspect execution metadata, but must not rewrite the already-recorded blind verdict.

A method result may reveal a benchmark anomaly, but it is never authority for changing a task, reference, annotation, or acceptance rule. Decide each correction from the task contract and method-independent source/reference evidence, without using which method benefits or how scores move. Bind that authority in the correction manifest and reject the revision before rescoring when it is absent. A source presentation state has no standalone meaning: apply it only through the benchmark's declared content scope.

Every triggered anomaly needs concrete evidence at each applicable layer, a root-cause classification, a deterministic equivalence scope expanded to the affected attempts, and one of: a justified no-change decision, a corrected reference/scorer with paired regressions, or a corrected request/adapter/runtime followed by the affected provider calls and renewed review. Missing raw/request evidence is `insufficient`, not proof that the provider failed. Do not publish with unresolved anomalies or a known evaluation defect. Report ordinary provider/origin limitations separately from evaluation defects, and do not retry a valid physical call merely because a later call might score better.

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

Before a cohort-wide offline recomputation, finish the paired controls and compare the known affected output family with its independent judgments. Cover equivalent serializers and adjacent invalid forms together, including visible text outside a link, image alternatives, and record ownership. Batch the shared corrections before the expensive sweep; do not rerun unchanged browser calls. Bound both worker count and queued payloads when saved outputs or logs are large.

A partial impact cache must retain each inherited record's actual scoring-source identity. Never stamp unchanged cached metrics with the new source hash. Select candidates through the same normalized fields as the changed parser; raw substring searches can miss labels formed by nested markup. Before accepting a partial sweep, check the normalized full-population candidates against the selected scope and rescore any uncovered candidates through the public scorer.

A subresource network error does not establish a main-navigation failure. Failure categories must agree with the reviewed cause and its actual stage; keep missing embedded content, unsupported direct-file conversion, empty output, and process crashes distinct. Interpretation pages show complete distributions and representative evidence. Full per-result diagnoses belong in the existing result inventory and details.

## Revision receipts

A score-only revision must record old and new dataset/result hashes, changed fields, decision changes, provider call count of zero, reviewer binding, and predecessor history hash. A new provider call must receive a new call identity and must not be described as a scoring correction. Before rescoring, validate the predecessor chain and every declared input-verdict binding, including its frozen predicate and source manifest. Reject incomplete provenance before scoring or writing revision history; do not discover it after processing the affected outputs.

Do not treat changed reference text or a list of flipped verdicts as a completed correction. Record the defect mechanism, not only its symptom, and bind each revision to compact synthetic regression cases that execute the same public scorer and task predicate as live evaluation. Include both a valid equivalent form and a nearby invalid form whenever the defect can create errors in both directions. Validate this provenance before writing the revised dataset or results; missing or unknown regression IDs fail closed.

## Native-worker caller audit

For a local URL-only comparison, verify the product's supported inner timeout and byte/character limits, not only the outer runner deadline or configuration JSON. Record the effective limits. For a fixed-native cohort, the common wall-clock budget belongs to the caller only: omit product timeout overrides and retain native internal timers. Supported capacity and extraction options may be fixed before the run; never patch product globals or supply per-case retrieval hints. A caller-created cap that truncates the fetched HTML before conversion is a measurement defect; retain the old calls and correct the affected complete method scope, rather than changing individual failed cases.

Run `harness.web_fetch.network_preflight.inspect_system_dns` before comparing local guarded fetchers with hosted products. Synthetic addresses in `198.18.0.0/15` make the environment unsuitable for this comparison: stop before product calls and restore a real-public-address DNS environment. Do not weaken product safety guards or replace individual case URLs to work around it. Retain the complete final worker response envelope as private evidence, alongside the normalized answer and public call log. Verify positive controls through the actual product entry point; existing scored outputs alone cannot prove that no response fields were dropped.


An explicitly frozen `public_doh` environment is a valid process-scoped DNS
correction when system DNS returns synthetic addresses. Apply one mode to all
supported native callers in the cohort, resolve every hostname with the same
rule, include DNS time in the shared call budget, bind resolver source files,
and verify both public retrieval and native rejection of private DNS answers.
Do not silently fall back to another resolver or disable a product guard. Record
unsupported methods and the local/hosted network asymmetry; do not imply the
machine's global DNS was changed.

Audit the declared structured output separately from model/UI presentation.
When a public schema exposes a complete HTML/text body, preserve it as the answer
rather than keeping only a lossy display projection. Preserve both fields in
private evidence. Never rescue an empty declared body from a nonempty display
message. Reproject retained full responses without new product calls when only
this selection changed; missing historical envelopes require fresh calls across
the full declared correction scope before claiming revised benchmark scores.
Reruns of unchanged compiled acceptance-contract tasks freeze the existing executable dataset. They do not require recovering historical qualification raw files or repeating admission review. Source qualification is part of task authoring and reference/rule correction, not a prerequisite for every provider-version comparison. Preserve historical receipts without applying their evidence-file requirements to a new execution protocol.
