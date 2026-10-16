# Evaluation of 100 URLs with observed access restrictions

This cohort asks whether Moli 1.1.11 can retrieve the declared content present on initial load at URLs where login, verification, anti-automation or access restrictions were previously observed. Calls do not click, type, scroll, paginate, log in or use OCR. Challenge pages, empty responses and body fragments do not satisfy the task.

The scope is 100 URL cases across 74 distinct sites. The executable `dataset.json` is external and bound by `source-inputs.json`; `dataset-catalog.json` retains the inventory and definition hashes. Import approved full references using the [reference-input guide](../../docs/reference-inputs.md); they are not distributed with source.

Body extraction is accepted against the declared body regions and composite record contracts. The compiler marks stable text as diagnostic for the 97 cases with either contract; the remaining three cases still require every stable-text group. All 1,586 independently observed stable-text items remain available for diagnostics. There are 79 body-region cases and 23 composite cases, including 21 quantity cases with 27 independent count groups for names, real links, paths, deduplication and same-record fields. Human rules, machine contracts, qualification and replacement provenance are held in `first-30-rule-review.md`, `remaining-70-rule-review.md`, `body-contracts.json`, `list-contracts.json` and `qualification/`. Qualification explains historical cohort admission; it is not a scoring input. Raw investigations remain outside Git. `run-profile.json` binds authoritative inputs, provider configuration, execution parameters and binary identity.

## Deterministic build and validation without calls

```sh
uv run --frozen python scripts/build_risk_control_dataset.py --output /tmp/risk-control-dataset.json
uv run --frozen python scripts/run_certified_benchmark.py risk-control --env-file .env
```

The first command rebuilds from hash-bound independent evidence under `BROWSER_EVAL_DATA_HOME` (default `~/.browser-eval`). The compiler emits the corrected stable-text roles. The historical profile and bound external dataset retain their original bytes; they must not be overwritten or treated as a certificate for the revised contract. Supply a new output path; frozen inputs cannot be overwritten. The second command validates the profile, dataset, Moli 1.1.11 version and binary hash without provider calls.

The compiler is `harness.web_fetch.risk_control_dataset`. When installed or called from another directory, pass `--definition-dir` with an explicit copy of this directory's rules and qualification manifests. Frozen-rule hashes are still enforced.

## Formal execution

```sh
uv run --frozen python scripts/run_certified_benchmark.py risk-control \
  --env-file .env --execute \
  --output "$BROWSER_EVAL_DATA_HOME/evidence/risk-control/<new-run-id>"
```

输出目录必须是新的，并位于数据目录下。正式入口固定为
`scripts/run_certified_benchmark.py risk-control`；直接对执行脚本使用 `--execute` 会被拒绝。
当前 profile 的新正式运行恰有 100 个逻辑结果，且不自动重试，并为每次调用在 Git 外保留完整、
哈希绑定的 debug stderr。保留的历史证据另有 3 个 DNS 失败后的用户授权补调，因此保留 103 次
物理调用；每例固定采用最后一次响应，全部调用耗时均计入，不能择优。运行前后 Moli 身份必须
一致；profile、已编译的数据集、规则和 provider 配置也会在调用前后核验。
普通重跑不读取历史资格原文，也不重做入选审核；修改题目、参考或规则时，才由构建器
核验相应来源。新运行生成 `frozen-input-verification.json` 和 v2 认证回执。
生成报告前校验 `certification.json` 对 profile、dataset、results、manifest、execution、
冻结输入核验回执和完整日志的绑定。历史 v1 回执继续按原证据契约读取，不改写或升级。

Moli identity must remain unchanged before and after calls. Each of the 100 declared references is checked before and after execution; missing, changed or incomplete scope stops certification. Report builds verify `certification.json` bindings to the profile, dataset, results, manifest, execution, reference-verification receipts and complete logs.

This directory contains no measured Moli outputs, raw provider artifacts, debug logs or credentials. Use the existing report builder with complete certified evidence. Historical results lacking pre/post-call reference certification cannot be promoted by writing a receipt afterward.

“Page body” means the declared body and record requirements within initial load. Missing navigation, login links or footer text cannot reject content that satisfies those requirements. Blocked-page detection, record counts, same-record fields and real links remain acceptance gates. Cases without a body or composite contract retain their stable-text gate.

## Current contract and historical results

The historical full-page contract required all 1,600 stable-text items. The body-scope correction assigns their acceptance role without changing the observed text or reference content. `research/risk-control-full-page-rule-revision.json` binds prior contracts and the full-page revision, retaining all registered peripheral text without rewriting earlier receipts or provider outputs. The historical 31/100 belongs to earlier inputs and evidence and is not a score for the current contract.

The current adapter obtains identity from `final_url`, status and final HTML in the same `fetch --dump json` response, then converts to Markdown within the same deadline. GET completion order in old debug logs cannot prove redirect ownership; insufficient identity evidence remains unknown. A changed call route needs new execution over the complete affected scope for formal scores and timing; old-route logs and timings cannot substitute.

## Frozen artifacts and language

The two rule files and the independent/completeness audits retain their original Chinese text. Their bytes are hash-bound by `run-profile.json` and revision provenance; the compiler reads the rule files directly. They are frozen acceptance inputs, not untranslated usage guides. Do not translate or rewrite them in place. The English [rule-authoring guide](rule-authoring-guide.md) explains the methodology for new definitions.
