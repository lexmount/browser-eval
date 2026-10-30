# Command scripts

Every file here is a thin command-line entry point. Parsing, validation,
execution and publication rules live in the owning `src/harness/` module.
Run Python entry points with `uv run --frozen python scripts/<name>.py` or the
matching `make` target.

A script stays in this directory only when the Makefile, CI, a maintained guide,
`configs/report-templates.json`, or a current profile, certificate or receipt
reaches it. A new entry point must be added to this inventory together with that
authority. Remove a script, the `main_*` function it exclusively wraps and its
exclusive tests in the same change once nothing listed below reaches it.

The "Reached from" column names the authority that keeps each script. Guides
are named by path; `make:` names a Makefile target.

## Shared setup and policy

| Script | Reached from |
| --- | --- |
| `import_benchmark_env.py` | `make import-env`, `SECURITY.md` |
| `import_reference_inputs.py` | `docs/reference-inputs.md`; error text in `lexbench_webfetch.inputs` |
| `bootstrap_oss_baselines.sh` | `docs/dependencies.md`, `docs/provider-contract.md` |
| `check_track_naming.py` | `make naming-check` (CI) |
| `report_template.py` | `make report-template`, `AGENTS.md` |
| `run_real_smoke.sh` | the documented runner for the opt-in `tests/test_real_smoke.py` live-provider class |

## Web Fetch execution and evidence

| Script | Reached from |
| --- | --- |
| `run_certified_benchmark.py` | `AGENTS.md`, `docs/provider-contract.md`, `evaluation/risk-control/run-profile.json`; runs reference freezing, postcheck, finalization, `risk-control`, `webmainbench-correction`, `wcxb` and `wcxb-latency` |
| `run_benchmark.py` | `AGENTS.md`, `docs/provider-contract.md`, bound latency certifiers |
| `run_headless_benchmark.py` | reproduction text rendered by `harness/web_fetch/report-translations.json` |
| `headless_worker.py` | executed by `harness/web_fetch/headless_runner.py` and `providers/headless.py` |
| `build_native_dns.py`, `capture_headless_browser_observations.py`, `collect_source_evidence.py`, `finalize_reference_checks.py`, `append_dynamic_content.py`, `merge_dynamic_supplement.py` | `src/harness/web_fetch/README.md` |
| `assemble_headless.py`, `check_headless_evidence.py`, `package_headless_resources.py` | `src/harness/web_fetch/HEADLESS.md` |
| `audit_initial_references.py` | `research/initial-reference-audit.json` |
| `add_provider_results.py` | sole entry to `evidence.add_provider_run`, which adds one complete provider cohort to the persistent current evidence |
| `certify_headless_cases.py`, `project_headless_provider.py` | sole entries to per-case certification and one-provider projection of a live-reference run (`HEADLESS.md`, "Capture, freeze and check references") |
| `build_postcheck_observations.py`, `check_resource_links.py` | sole entries to independent post-call observation binding and frozen resource-link checks |
| `select_headless_speed_cases.py`, `package_headless_speed.py` | sole entries to the provider-blind speed cohort that `assemble_headless.py --speed-cohort` consumes |
| `assemble_webfetch_dataset.py` | compiles `evaluation/webfetch-seven-family-430/dataset-assembly.json` |
| `assemble_webfetch_refresh.py`, `audit_fixed_native_comparison.py` | sole entries to declared cohort refresh assembly and the two-build native comparison audit |
| `probe_cowagent_text.py` | writes the `guide/cowagent-text-probe.json` evidence that `report_build.py` publishes |

## Web Fetch review, rescoring and revision

| Script | Reached from |
| --- | --- |
| `review_headless_results.py`, `review_result_anomalies.py`, `merge_result_review_revision.py`, `rebase_blind_result_review.py`, `rescore_content.py`, `revise_body_scoring.py`, `revise_reference_evidence.py` | `src/harness/web_fetch/README.md` |
| `prepare_result_review_subset.py`, `merge_internal_review_shards.py`, `rebind_result_review_metadata.py` | sole entries to the shared independent-review contract in `result_review.py`; exercised by `tests/package_smoke.py` |

## Web Fetch reports and publication

| Script | Reached from |
| --- | --- |
| `build_report.py` | `make report`, `configs/report-templates.json`, `AGENTS.md` |
| `check_report.py`, `check_report_links.py`, `check_report_revision.py` | `make report-check`, `make wcxb-release-check` |
| `check_diagnostic_report.py` | `make webfetch-diagnostic-report` |
| `check_wcxb_visual.py` | `make wcxb-release-check` |
| `check_publication_bundle.py` | `src/harness/web_fetch/README.md` |
| `restore_report_content.py` | `docs/architecture.md` |
| `package_artifact_hub.py`, `publish_artifact_hub.py`, `publish_wcxb_artifact_site.py` | `configs/report-templates.json`, `docs/benchmarks.md` |
| `authorize_artifact_hub.py` | saves the user-scoped Artifact Site token that `AGENTS.md` requires for publishing |
| `rename_artifact_hub.py`, `verify_artifact_hub.py` | Artifact Site maintenance; `verify_artifact_hub.py` is executed by `public_webarena/release.py` |

## WCXB and WebMainBench

| Script | Reached from |
| --- | --- |
| `score_wcxb_results.py` | `src/harness/web_fetch/README.md`, WCXB reproduction text in `report.py` |
| `select_wcxb_sample.py` | regenerates `evaluation/public-wcxb-test-qualified.json`, the manifest named by the WCXB reproduction commands in `report.py` |
| `run_wcxb_paired_latency.py` | collects the paired timing cohort that `run_certified_benchmark.py wcxb-latency` certifies |
| `select_public_benchmark_sample.py` | `make public-benchmark-sample` |
| `run_webmainbench_benchmark.py`, `score_webmainbench_results.py`, `review_webmainbench_results.py`, `assemble_webmainbench_evidence.py` | `make webmainbench-run`, `-score`, `-review`, `-assemble` |
| `audit_webmainbench_suspicions.py`, `apply_webmainbench_case_correction.py` | `make webmainbench-audit`, `make webmainbench-apply-case-correction` |
| `apply_webmainbench_latency_correction.py`, `assemble_webmainbench_full.py`, `correct_webmainbench_scoring.py`, `correct_webmainbench_teds.py`, `rescore_webmainbench_case_correction.py`, `review_webmainbench_teds_correction.py`, `run_certified_webmainbench_full.py`, `score_certified_webmainbench_full.py` | `src/harness/web_fetch/WEBMAINBENCH.md` |
| `run_certified_webmainbench_latency.py` | `src/harness/web_fetch/data/webmainbench-latency-source.json` |
| `diagnose_webmainbench_moli_tail.py` | `docs/architecture.md`, bound latency certifiers |
| `review_webmainbench_content.py` | `docs/architecture.md` |
| `check_webmainbench_report.py` | release gate for the frozen WebMainBench sample report (`--authority-root`) |
| `check_webmainbench_full_report.py` | standalone release gate for the complete WebMainBench report; the build runs the same check |

## Web Search

| Script | Reached from |
| --- | --- |
| `build_search_report.py` | `configs/report-templates.json`, `src/harness/web_search/README.md` |
| `review_search_results.py`, `publish_search_report.py` | `src/harness/web_search/README.md` |
| `judge_search_relevance.py` | `src/harness/web_search/README.md`, benchmark-report skill |
| `check_search_report_visual.py` | `src/harness/web_search/visual.py` |

## Browser Use

| Script | Reached from |
| --- | --- |
| `build_browser_use_report.py`, `check_browser_use_report.py`, `check_browser_use_visual.py`, `review_browser_use_results.py` | `make browser-use-*` |
| `package_browser_use_artifact_hub.py` | `configs/report-templates.json`, `docs/benchmarks.md` |
| `publish_browser_use_artifact_hub.py` | publishes the packaged Browser Use report to its bound target |

## Browser Trajectory Replay

| Script | Reached from |
| --- | --- |
| `build_webarena_replay_report.py`, `check_webarena_replay_report.py`, `check_webarena_replay_visual.py`, `package_webarena_replay_artifact_site.py`, `publish_webarena_replay_artifact_site.py` | `make trajectory-replay-*` |
| `run_webarena_catalog.py`, `build_webarena_catalog_report.py`, `check_webarena_catalog_release.py`, `audit_webarena_catalog_acceptance.py`, `audit_webarena_catalog_actions.py`, `build_webarena_catalog_comparison_inputs.py`, `consolidate_webarena_catalog.py`, `rebind_webarena_catalog.py` | `make webarena-catalog-*`; the exact set is pinned by `check_track_naming.py` |
| `aggregate_webarena_three_method_cohorts.py`, `freeze_webarena_three_method_inputs.py`, `run_remote_three_method_cohort.py` | `evaluation/trajectory-replay/webarena-verified/README.md` |
| `use_prepared_webarena_cohort_reset.py` | the `--cohort-reset-hook` of `run_remote_three_method_cohort.py`, hash-bound in its receipts |
| `build_browser_use_replay_seeds.py`, `build_webarena_expert_seeds.py`, `run_webarena_trajectory_replay.py`, `promote_webarena_trajectories.py` | seed, replay and promotion stages of the repository-curated dataset described in `evaluation/trajectory-replay/webarena-verified/README.md` |
| `merge_webarena_replay_results.py` | overlays formal correction shards onto the current replay matrix |
| `apply_webarena_qualification.py`, `audit_replay_cart_assertion.py`, `audit_replay_comparison_inputs.py`, `audit_replay_readiness.py`, `audit_saved_replay_correction.py`, `audit_webarena_recorded_outcomes.py`, `audit_webarena_traces.py`, `build_revised_webarena_dataset.py`, `build_webarena_codex_repair_suite.py`, `build_webarena_native_scope.py`, `build_webarena_repair_batch.py`, `build_webarena_repair_packet.py`, `build_webarena_repair_probe.py`, `build_webarena_repair_queue.py`, `build_webarena_repair_validation_suite.py`, `build_webarena_suite.py`, `fetch_webarena_traces.py`, `freeze_original36_candidate_cohort.py`, `reevaluate_webarena_evaluator_transition.py`, `rescore_saved_retrieval_attempt.py`, `revise_webarena_method_inputs.py`, `run_remote_mutation_diagnostic.py`, `run_webarena_codex_repairs.py`, `run_webarena_qualification.py`, `smoke_webarena_runtime.py`, `supersede_webarena_task_browser_results.py`, `verify_webarena_evaluator_exclusions.py` | `docs/trajectory-replay-protocol.md` |
| `propose_webarena_action_repair.py`, `propose_webarena_autowait_repairs.py`, `propose_webarena_capture_target.py`, `propose_webarena_evaluator_fill_repair.py`, `propose_webarena_observed_repair.py`, `propose_webarena_replace_target.py` | `docs/trajectory-replay-protocol.md`, "Public WebArena repair proposals" |
| `build_webarena_authoring_suite.py`, `build_webarena_observation_suite.py`, `build_webarena_evidence_revalidation_suite.py`, `build_webarena_qualification_subset.py`, `build_webarena_requalification_suite.py`, `finalize_webarena_adjudication.py` | `docs/trajectory-replay-protocol.md`, "Qualification decisions and immutable outputs" (queue, revalidation and finalization commands of `public_webarena/qualification.py`) |
| `build_webarena_trace_catalog.py` | rebuilds the checked-in 179-task human-trace catalog from official inputs |
| `release_webarena_trajectory_report.py` | `make public-webarena-report`, `-audit`, `-release`; it checks and packages the bundle itself |
| `publish_public_webarena_report.py` | executed by `public_webarena/release.py` |
| `lock_webarena_images.py`, `manage_webarena_environment.py`, `manage_webarena_rainbond_pool.py`, `verify_webarena_env_contract.py`, `verify_webarena_map.py` | `deploy/webarena/README.md`, `docs/webarena-fixed-trajectory-reproduction.md`, `examples/trajectory_replay/public_webarena/README.md` |

## Lexbench resource report

`moli_report/` holds the Lexbench resource-report renderer and its collectors;
`moli_report/BUILD.md` documents them. Resource charts render through
`uv run --frozen python -m harness.lexbench.charts`.
