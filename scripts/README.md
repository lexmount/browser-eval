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

The "Reached from" column names the authority that keeps each script; that
authority names the script by basename or module path. Guides are named by
path; `make` names a Makefile target. Scripts that no such authority names yet
are listed under "Pending owner decision (#93)" with the reason each one
survived this pruning.

## Shared setup and policy

| Script | Reached from |
| --- | --- |
| `import_benchmark_env.py` | `make import-env`, `SECURITY.md` |
| `import_reference_inputs.py` | `docs/reference-inputs.md`; error text in `lexbench_webfetch.inputs` |
| `bootstrap_oss_baselines.sh` | `docs/dependencies.md`, `docs/provider-contract.md` |
| `check_package.py` | `make package-check` (CI) |
| `check_track_naming.py` | `make naming-check` (CI) |
| `report_template.py` | `make report-template`, `AGENTS.md` |

## Web Fetch execution and evidence

| Script | Reached from |
| --- | --- |
| `run_certified_benchmark.py` | `AGENTS.md`, `docs/provider-contract.md`, `evaluation/risk-control/run-profile.json`; runs reference freezing, postcheck, finalization, `risk-control`, `webmainbench-correction`, `wcxb` and `wcxb-latency` |
| `run_benchmark.py` | `AGENTS.md`, `docs/provider-contract.md`, bound latency certifiers |
| `run_headless_benchmark.py` | reproduction text rendered by `src/harness/web_fetch/report-translations.json` |
| `headless_worker.py` | executed by `src/harness/web_fetch/headless_runner.py` and `src/harness/web_fetch/providers/headless.py` |
| `build_native_dns.py`, `capture_headless_browser_observations.py`, `collect_source_evidence.py`, `finalize_reference_checks.py`, `append_dynamic_content.py`, `merge_dynamic_supplement.py` | `src/harness/web_fetch/README.md` |
| `assemble_headless.py`, `check_headless_evidence.py`, `package_headless_resources.py` | `src/harness/web_fetch/HEADLESS.md` |
| `audit_initial_references.py` | `research/initial-reference-audit.json` |

## Web Fetch review, rescoring and revision

| Script | Reached from |
| --- | --- |
| `review_headless_results.py`, `review_result_anomalies.py`, `merge_result_review_revision.py`, `rebase_blind_result_review.py`, `rescore_content.py`, `revise_body_scoring.py`, `revise_reference_evidence.py` | `src/harness/web_fetch/README.md` |

## Web Fetch reports and publication

| Script | Reached from |
| --- | --- |
| `build_report.py` | `make report`, `configs/report-templates.json`, `AGENTS.md` |
| `check_report.py`, `check_report_links.py`, `check_report_revision.py` | `make report-check`, `make wcxb-release-check` |
| `check_diagnostic_report.py` | `make webfetch-diagnostic-report` |
| `check_publication_bundle.py` | `src/harness/web_fetch/README.md` |
| `verify_artifact_site.py` | executed by `src/harness/trajectory_replay/public_webarena/release.py` |
| `restore_report_content.py` | `docs/architecture.md` |
| `package_artifact_site.py`, `publish_artifact_site.py` | `configs/report-templates.json`, `docs/benchmarks.md` |

## WCXB and WebMainBench

WCXB and WebMainBench stages run through `uv run --frozen python -m harness.web_fetch <command>`,
beside the `run`, `report`, `rescore`, `validate` and `accounts` commands; `--help` lists
them. `make wcxb-release-check`, `make public-benchmark-sample` and `make webmainbench-*`
reach the commands they name; the [Web Fetch guide](../src/harness/web_fetch/README.md),
[WebMainBench guide](../src/harness/web_fetch/WEBMAINBENCH.md),
[`docs/architecture.md`](../docs/architecture.md),
[`docs/benchmarks.md`](../docs/benchmarks.md) and `configs/report-templates.json` show the rest,
except the three commands listed under "Pending owner decision (#93)".
The scripts below stay because frozen latency certifiers name them.

| Script | Reached from |
| --- | --- |
| `run_certified_webmainbench_latency.py` | `src/harness/web_fetch/data/webmainbench-latency-source.json` |
| `diagnose_webmainbench_moli_tail.py` | `docs/architecture.md`, bound latency certifiers |
| `run_webmainbench_benchmark.py` | `make webmainbench-run`, bound latency certifiers |

## Web Search

Report commands run through `uv run --frozen python -m harness.web_search <command>`; the
[track guide](../src/harness/web_search/README.md) lists them.

## Browser Use

Report commands run through `uv run --frozen python -m harness.browser_use <command>`:
`report`, `check`, `visual`, `review`, `package` and `publish`. `make browser-use-*` runs
`report`, `review`, `visual` and `check`; [`docs/benchmarks.md`](../docs/benchmarks.md) shows
`report`, `check` and `package`. `publish` has neither: run it directly after `package`.

## Browser Trajectory Replay

The curated track runs through `uv run --frozen python -m harness.trajectory_replay <command>`;
`--help` lists every command. `make trajectory-replay-*` runs `report`, `check`, `visual`,
`package` and `publish`, and `make webarena-catalog-*` runs `catalog-report` and
`catalog-release-check`. `examples/trajectory_replay/run.sh` runs `evaluate`;
[`docs/benchmarks.md`](../docs/benchmarks.md),
[`docs/trajectory-replay-protocol.md`](../docs/trajectory-replay-protocol.md) and
[`evaluation/trajectory-replay/webarena-verified/README.md`](../evaluation/trajectory-replay/webarena-verified/README.md)
show the rest, except the commands listed under "Pending owner decision (#93)".
`check_track_naming.py` rejects any new `*webarena_catalog*` script.

The scripts below stay outside that entry point for the reason in each row.

| Script | Reached from |
| --- | --- |
| `run_remote_three_method_cohort.py` | `evaluation/trajectory-replay/webarena-verified/README.md`; `remote_execution.cohort_reference_hashes` binds this file's hash as `command_entry` in every cohort certificate |
| `apply_webarena_qualification.py`, `audit_webarena_recorded_outcomes.py`, `audit_webarena_traces.py`, `build_webarena_codex_repair_suite.py`, `build_webarena_native_scope.py`, `build_webarena_repair_batch.py`, `build_webarena_repair_packet.py`, `build_webarena_repair_probe.py`, `build_webarena_repair_queue.py`, `build_webarena_repair_validation_suite.py`, `build_webarena_suite.py`, `fetch_webarena_traces.py`, `run_webarena_codex_repairs.py`, `run_webarena_qualification.py`, `smoke_webarena_runtime.py`, `supersede_webarena_task_browser_results.py`, `verify_webarena_evaluator_exclusions.py` | Public WebArena replay, isolated from the curated entry point: `docs/trajectory-replay-protocol.md` |
| `propose_webarena_action_repair.py`, `propose_webarena_autowait_repairs.py`, `propose_webarena_capture_target.py`, `propose_webarena_evaluator_fill_repair.py`, `propose_webarena_observed_repair.py`, `propose_webarena_replace_target.py` | Public WebArena replay: `docs/trajectory-replay-protocol.md`, "Public WebArena repair proposals" |
| `release_webarena_trajectory_report.py` | Public WebArena replay: `make public-webarena-report`, `-audit`, `-release`; it checks and packages the bundle itself |
| `publish_public_webarena_report.py` | Public WebArena replay: executed by `src/harness/trajectory_replay/public_webarena/release.py` |
| `lock_webarena_images.py`, `manage_webarena_environment.py`, `manage_webarena_rainbond_pool.py`, `verify_webarena_env_contract.py`, `verify_webarena_map.py` | WebArena environment, owned by `harness.environments.webarena`: `deploy/webarena/README.md`, `docs/webarena-fixed-trajectory-reproduction.md`, `examples/trajectory_replay/public_webarena/README.md` |

## Pending owner decision (#93)

No Makefile target, CI job, guide, `configs/report-templates.json` entry or
checked-in profile, certificate or receipt names these entries. Each one is
kept for now because it is the only command entry to a stage that a current
harness module still implements, so deleting it would remove that stage rather
than a duplicate. Its owner must either name it in the owning guide (and move
the row to the table above) or delete the stage, its `main_*` function and its
exclusive tests together.

| Script or command | Why it survived |
| --- | --- |
| `add_provider_results.py` | sole entry to `evidence.add_provider_run`, which adds one complete provider cohort to the persistent current evidence |
| `certify_headless_cases.py`, `project_headless_provider.py` | sole entries to per-case certification and one-provider projection of a live-reference run; `HEADLESS.md` describes the stage without naming them |
| `build_postcheck_observations.py`, `check_resource_links.py` | sole entries to independent post-call observation binding and frozen resource-link checks |
| `select_headless_speed_cases.py`, `package_headless_speed.py` | sole entries to the provider-blind speed cohort that `assemble_headless.py --speed-cohort` consumes |
| `assemble_webfetch_dataset.py` | compiles `evaluation/webfetch-seven-family-430/dataset-assembly.json`, which does not name the script |
| `assemble_webfetch_refresh.py`, `audit_fixed_native_comparison.py` | sole entries to declared cohort refresh assembly and the two-build native comparison audit |
| `probe_cowagent_text.py` | writes the `guide/cowagent-text-probe.json` evidence that `report_build.py` publishes; only that output file is named |
| `prepare_result_review_subset.py`, `merge_internal_review_shards.py`, `rebind_result_review_metadata.py` | sole entries to the shared independent-review contract in `result_review.py`; only `tests/package_smoke.py` runs them |
| `harness.web_fetch wcxb-select` | regenerates `evaluation/public-wcxb-test-qualified.json`; only tests name the command |
| `harness.web_fetch webmainbench-check`, `harness.web_fetch webmainbench-full-check` | standalone release gates for the WebMainBench sample and complete reports; `report_build.py` imports the full-report check, not the command |
| `authorize_artifact_site.py` | saves the user-scoped Artifact Site token that `AGENTS.md` describes, without naming the script |
| `rename_artifact_site.py` | Artifact Site display-title maintenance |
| `use_prepared_webarena_cohort_reset.py` | passed as `run_remote_three_method_cohort.py --cohort-reset-hook`; receipts outside Git bind its hash, not its name |
| `harness.trajectory_replay seeds`, `expert-seeds`, `replay`, `promote` | seed, replay and promotion stages of the repository-curated dataset; `evaluation/trajectory-replay/webarena-verified/README.md` describes the stages without naming the commands |
| `harness.trajectory_replay merge-results` | overlays formal correction shards onto the current replay matrix |
| `harness.trajectory_replay catalog-audit-acceptance`, `catalog-audit-actions` | sole entries to the catalog sweep and failed-repair audit and the evaluate-action audit |
| `harness.trajectory_replay catalog-consolidate`, `catalog-rebind` | sole entries to freezing accepted trajectories into one deployment-bound catalog and binding a frozen catalog to another site deployment |
| `harness.trajectory_replay catalog-comparison-inputs` | sole entry to compiling catalog replay evidence into shared comparison report inputs |
| `build_webarena_authoring_suite.py`, `build_webarena_observation_suite.py`, `build_webarena_evidence_revalidation_suite.py`, `build_webarena_qualification_subset.py`, `build_webarena_requalification_suite.py`, `finalize_webarena_adjudication.py` | queue, revalidation and finalization commands of `src/harness/trajectory_replay/public_webarena/qualification.py`; "Qualification decisions and immutable outputs" in `docs/trajectory-replay-protocol.md` describes them without naming the scripts |
| `build_webarena_trace_catalog.py` | rebuilds the checked-in 179-task human-trace catalog from official inputs |
| `run_real_smoke.sh` | runner for the opt-in `tests/test_real_smoke.py` live-provider class; only that test names it |

## Lexbench resource report

`moli_report/` holds the Lexbench resource-report renderer and its collectors;
`moli_report/BUILD.md` documents them. Resource charts render through
`uv run --frozen python -m harness.lexbench.charts`.
