# Evaluation of 100 URLs with observed access restrictions

This cohort asks whether Moli 1.1.11 can retrieve the declared content present on initial load at URLs where login, verification, anti-automation or access restrictions were previously observed. Calls do not click, type, scroll, paginate, log in or use OCR. Challenge pages, empty responses and body fragments do not satisfy the task.

The scope is 100 URL cases across 74 distinct sites. The executable `dataset.json` is external and bound by `source-inputs.json`; `dataset-catalog.json` retains the inventory and definition hashes. Import approved full references using the [reference-input guide](../../docs/reference-inputs.md); they are not distributed with source.

Every case requires all declared stable text from initial load, including registered navigation, buttons and sidebars. Missing any required phrase fails. Another 79 cases require complete body regions; 21 cases use 27 independent count groups for names, real links, paths, deduplication and same-record fields. Human rules, machine contracts, qualification and replacement provenance are held in `first-30-rule-review.md`, `remaining-70-rule-review.md`, `body-contracts.json`, `list-contracts.json` and `qualification/`. Qualification explains historical cohort admission; it is not a scoring input. Raw investigations remain outside Git. `run-profile.json` binds authoritative inputs, provider configuration, execution parameters and binary identity.

## Deterministic build and validation without calls

```sh
uv run --frozen python scripts/build_risk_control_dataset.py --output /tmp/risk-control-dataset.json
uv run --frozen python scripts/run_certified_benchmark.py risk-control --env-file .env
```

The first command rebuilds from hash-bound independent evidence under `BROWSER_EVAL_DATA_HOME` (default `~/.browser-eval`). Output must match the bound external dataset byte for byte. Supply a new output path; frozen inputs cannot be overwritten. The second command validates the profile, dataset, Moli 1.1.11 version and binary hash without provider calls.

The compiler is `harness.web_fetch.risk_control_dataset`. When installed or called from another directory, pass `--definition-dir` with an explicit copy of this directory's rules and qualification manifests. Frozen-rule hashes are still enforced.

## Formal execution

```sh
uv run --frozen python scripts/run_certified_benchmark.py risk-control \
  --env-file .env --execute \
  --output "$BROWSER_EVAL_DATA_HOME/evidence/risk-control/<new-run-id>"
```

Output must be a new directory under the data root. The certified entry point is `scripts/run_certified_benchmark.py risk-control`; direct `--execute` on the execution script is rejected. A new run under the current profile has exactly 100 logical results and no automatic retries, retaining complete hash-bound debug stderr outside Git. Historical evidence additionally contains three user-authorized calls after DNS failures: 103 physical calls in total. The final response is authoritative, all call costs remain recorded and no best-response selection is allowed.

Moli identity must remain unchanged before and after calls. Each of the 100 declared references is checked before and after execution; missing, changed or incomplete scope stops certification. Report builds verify `certification.json` bindings to the profile, dataset, results, manifest, execution, reference-verification receipts and complete logs.

This directory contains no measured Moli outputs, raw provider artifacts, debug logs or credentials. Use the existing report builder with complete certified evidence. Historical results lacking pre/post-call reference certification cannot be promoted by writing a receipt afterward.

“Full page” means the declared stable text, body and list requirements within initial load. It does not claim every undeclared character or content revealed by interaction. Registered navigation text is as mandatory as body text.

## Current contract and historical results

The current contract requires 1,600 stable-text items. `research/risk-control-full-page-rule-revision.json` binds prior contracts and the full-page revision, retaining all registered peripheral text without rewriting earlier receipts or provider outputs. The historical 31/100 belongs to earlier inputs and evidence and is not a score for the current contract.

The current adapter obtains identity from `final_url`, status and final HTML in the same `fetch --dump json` response, then converts to Markdown within the same deadline. GET completion order in old debug logs cannot prove redirect ownership; insufficient identity evidence remains unknown. A changed call route needs new execution over the complete affected scope for formal scores and timing; old-route logs and timings cannot substitute.

## Frozen artifacts and language

The two rule files and the independent/completeness audits retain their original Chinese text. Their bytes are hash-bound by `run-profile.json` and revision provenance; the compiler reads the rule files directly. They are frozen acceptance inputs, not untranslated usage guides. Do not translate or rewrite them in place. The English [rule-authoring guide](rule-authoring-guide.md) explains the methodology for new definitions.
