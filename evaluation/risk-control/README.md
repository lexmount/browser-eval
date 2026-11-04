# Evaluation of 100 URLs with observed access restrictions

This cohort asks whether Moli 1.1.11 can retrieve the declared content present on initial load at URLs where login, verification, anti-automation or access restrictions were previously observed. Calls do not click, type, scroll, paginate, log in or use OCR. Challenge pages, empty responses and body fragments do not satisfy the task.

The scope is 100 URL cases across 74 distinct sites. The executable `dataset.json` is external and bound by `source-inputs.json`; `dataset-catalog.json` retains the inventory and definition hashes. Import approved full references using the [reference-input guide](../../docs/reference-inputs.md); they are not distributed with source.

Body extraction is accepted against the declared body regions and composite record contracts. The compiler marks stable text as diagnostic for the 97 cases with either contract; the remaining three cases still require every stable-text group. All 1,586 independently observed stable-text items remain available for diagnostics. There are 79 body-region cases and 23 composite cases, including 21 quantity cases with 27 independent count groups for names, real links, paths, deduplication and same-record fields. Human rules, machine contracts, qualification and replacement provenance are held in `first-30-rule-review.md`, `remaining-70-rule-review.md`, `body-contracts.json`, `list-contracts.json` and `qualification/`. Qualification explains historical cohort admission; it is not a scoring input. Raw investigations remain outside Git. `run-profile.json` binds authoritative inputs, provider configuration, execution parameters and binary identity.

## Deterministic build and validation without calls

```sh
uv run --frozen lexbench-webfetch compile-access-restrictions --output /tmp/risk-control-dataset --evidence-home ~/.browser-eval
uv run --frozen python scripts/run_certified_benchmark.py risk-control --env-file .env
```

The first command rebuilds from hash-bound independent evidence under `BROWSER_EVAL_DATA_HOME` (default `~/.browser-eval`). The compiler emits the corrected stable-text roles. The historical profile and bound external dataset retain their original bytes; they must not be overwritten or treated as a certificate for the revised contract. Supply a new output path; frozen inputs cannot be overwritten. The second command validates the profile, dataset, Moli 1.1.11 version and binary hash without provider calls.

The compiler is `lexbench_webfetch.risk_control_dataset`. By default it reads its rules and qualification manifests from the pinned definition directory that `definition_path("evaluation/risk-control")` resolves to. To supply them explicitly, pass `--definition-dir` with a copy of that pinned directory; this checkout directory does not hold those files. Frozen-rule hashes are still enforced.

## Formal execution

```sh
uv run --frozen python scripts/run_certified_benchmark.py risk-control \
  --env-file .env --execute \
  --output "$BROWSER_EVAL_DATA_HOME/evidence/risk-control/<new-run-id>"
```

The output directory must be new and under the data directory. The formal entry is fixed as `scripts/run_certified_benchmark.py risk-control`; using `--execute` directly on the execution script is rejected. A new formal run of the current profile has exactly 100 logical results, with no automatic retry, and keeps complete, hash-bound debug stderr outside Git for every call. The retained historical evidence has 3 additional user-authorized calls after DNS failures, so it retains 103 physical calls; each case uses its last response, every call's duration counts, and the best response cannot be selected.

Moli identity must remain unchanged before and after calls. The profile, compiled dataset, rules and provider configuration are also verified before and after calls. These frozen inputs and the other profile-bound source files are hash-checked as one snapshot, not reference by reference; only historical v1 receipts record a per-case reference-proof check. A missing or changed frozen input, or an incomplete 100-result matrix, stops certification.

An ordinary rerun does not read historical qualification raw files or repeat admission review; only changing tasks, references or rules makes the builder verify the corresponding sources. A new run produces `frozen-input-verification.json` and a v2 certification receipt. Report builds verify `certification.json` bindings to the profile, dataset, results, manifest, execution, frozen-input verification receipt and complete logs. Historical v1 receipts are still read under their original evidence contract and are not rewritten or upgraded.

This directory contains no measured Moli outputs, raw provider artifacts, debug logs or credentials. Use the existing report builder with complete certified evidence. Historical results lacking pre/post-call reference certification cannot be promoted by writing a receipt afterward.

“Page body” means the declared body and record requirements within initial load. Missing navigation, login links or footer text cannot reject content that satisfies those requirements. Blocked-page detection, record counts, same-record fields and real links remain acceptance gates. Cases without a body or composite contract retain their stable-text gate.

## Current contract and historical results

The historical full-page contract required all 1,600 stable-text items. The body-scope correction assigns their acceptance role without changing the observed text or reference content. `research/risk-control-full-page-rule-revision.json` binds prior contracts and the full-page revision, retaining all registered peripheral text without rewriting earlier receipts or provider outputs. The historical 31/100 belongs to earlier inputs and evidence and is not a score for the current contract.

The current adapter obtains identity from `final_url`, status and final HTML in the same `fetch --dump json` response, then converts to Markdown within the same deadline. GET completion order in old debug logs cannot prove redirect ownership; insufficient identity evidence remains unknown. A changed call route needs new execution over the complete affected scope for formal scores and timing; old-route logs and timings cannot substitute.

## Frozen artifacts and language

The two rule files and the independent/completeness audits retain their original Chinese text. Their bytes are hash-bound by `run-profile.json` and revision provenance; the compiler reads the rule files directly. They are frozen acceptance inputs, not untranslated usage guides. Do not translate or rewrite them in place. The English rule-authoring guide (`evaluation/risk-control/rule-authoring-guide.md` in the pinned benchmark definitions) explains the methodology for new definitions.
