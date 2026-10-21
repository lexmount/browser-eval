# Build and verify the Moli report

The Moli comparison report adapter lives in `harness/lexbench/`; this directory contains its command entries and reader assets. Measured evidence stays outside Git under `BROWSER_EVAL_DATA_HOME` (default: `~/.browser-eval`). It does not use the Web Fetch publication target.

The frozen Linux runner inputs are read from the selected profile’s `paths.runner`; `LEXBENCH_REPORT_RUNNER_ROOT` can override that path. Report generation verifies their recorded hashes without importing or executing their Python files. `harness/resource_calibration.py` verifies complete profiler-off and profiler-on pairs and their shared experimental provenance. Its calculation is derived from the frozen Lexbench source, with Apache-2.0 attribution and license retained in the repository and installation package. All seven resource configurations use Linux ARM64 release binaries, PSS and process-tree CPU measurements in Docker on the Mac host.

The report profile and chart payload have separate schemas. In `moli_report_profile/1`, resource `tasks` and `attempts_per_task` map to the chart's `total_tasks` and `calls_per_task`; `chart_tasks` and `chart_calls` retain their names. The builder validates the measured populations before constructing the chart payload. Do not pass the profile directly to the plotting API.

Build from validated fixed-layout functional summaries and the complete resource profile directory:

```sh
uv run --frozen python scripts/moli_report/build.py \
  --profile /path/to/report-profile.json \
  --output /tmp/moli-report-release \
  --fixed-summary 119_off /path/to/patched-off-summary.json \
  --fixed-summary 119_on /path/to/patched-on-summary.json \
  --fixed-summary 119_official_off /path/to/official-off-summary.json \
  --fixed-summary 119_official_on /path/to/official-on-summary.json \
  --linux-resource-profiles /path/to/completed-linux-resource-profiles
uv run --frozen python scripts/moli_report/verify.py --site /tmp/moli-report-release --release
```

The public builder falls back to English. Set `BROWSER_EVAL_REPORT_LANGUAGE=zh` or pass `--language zh` for a Chinese-only build; `--include-english` keeps both editions when Chinese is primary. `report-languages.json` records the editions used by verification, packaging and browser checks. Without English, both English entry paths show a notice with a return link. Translation sources remain available for manual generation.

Choose a new output directory for each build; the builder does not delete existing output. `--vendor-dir` selects the retained Mermaid distribution and license. Their hashes are checked before copying. The profile supplies its location under the data home; no vendored executable or measured output is committed here.

`--profile` is required and selects the report instance. Its `paths` object owns the cohort, runner, output, vendor assets, correction inputs and version comparison inputs. Relative evidence paths use the data home; the template source is relative to the repository. Explicit CLI paths override the corresponding profile entries. Chart filenames use the shared `comparison-` prefix. `--cohort` selects the functional cohort and its adjacent `protocol.json` and `certification.json`; unless supplied separately with `--summary`, `report-runs.json` comes from the same directory. Importing the adapter or requesting command help does not read private evidence. Evidence validation remains enabled under Python optimization.

The base three-configuration diagnostic report and partial fixed-layout additions retain their method summary and interpretation navigation. The 1.1.9 comparison chapter is rendered only when both compared configurations exist. This does not relax the complete method matrix required by `--release`.

The fixed-layout reproduction entry delegates to `harness/lexbench/reproduction.py`:

```sh
uv run --frozen python scripts/moli_report/run.py \
  --cohort /path/to/cohort.json --protocol /path/to/protocol.json \
  --repo /path/to/frozen-runner --output /path/to/evidence \
  --image sha256:CAPTURED_IMAGE_HASH --node-modules /path/to/linux-node-modules \
  --agent-browser /path/to/linux-agent-browser --mode off --phase baseline
```

The default operation validates inputs and prints the Docker command. `--execute` starts the selected run; it preserves existing runs and rechecks frozen inputs afterward. The protocol and cohort must describe the same complete 372-case, three-attempt scope. Report bundles include this module as `reproduction/run.py`, with adjacent `cohort.json` and `protocol.json` as defaults; install Browser Eval before using it. Python optimization does not disable input checks. Historical source and measured-call bindings are unchanged.

The four 1.1.9 resource configurations are release builds bound by `resource_builds` in `/path/to/report-profile.json`. Functional evidence binds its own binary identity. Do not relabel retained functional outcomes as a new build's measured accuracy.

`uv run --frozen python scripts/moli_report/run_linux_resource_profiles.py --protocol /path/to/frozen-protocol.json --output /path/to/new-evidence-directory` executes the unchanged Lexbench driver and resource collector. Freeze 557 task definitions, fixtures, runner files, four binary hashes, five attempts, jobs=1, the seed and the 250 ms interval before execution. Each configuration has an independent container; all runs execute serially. The official profiler-off calibration precedes the profiler-on measurement. Resource values come from the latter. The builder verifies all 11,140 measured calls and their calibration pairs, checks the frozen 1,045-call chart population, recomputes aggregates and rejects missing or changed evidence. Keep all measured files outside Git.

`harness/lexbench/resource_profiles.py` owns this frozen Linux profile protocol. New calls bind that module’s hash as `orchestrator_sha256` before execution; moving an entry script does not change saved historical calls. The executor validates the same task and chart populations as the aggregator, freezes the exact protocol and collector bytes, and checks both before and after every phase. A changed protocol stops the queue before the next call and cannot produce a completed receipt. Saved cohorts retain their own `orchestrator.py`; aggregation verifies those bytes without importing or executing them.

The static verifier checks score/evidence bindings, resource populations and percentages, fixed-case log links, charts, rendering for the generated editions, unique resource extrema and negative controls. Its implementation is `harness/lexbench/verification.py`; the JavaScript rendering contract is an installed resource under `harness/lexbench/assets/`, also exercised by `tests/test_moli_report_render_contract.py`. Importing the verifier does not parse command arguments or read evidence. `--release --profile /path/to/profile.json` selects the authoritative method and binary bindings explicitly; an installed distribution requires this option when the repository profile is unavailable. `verify_evidence(site)` checks saved evidence and its negative controls; the full `verify(site)` command additionally runs the rendering contract and remains the release check.

For release verification, serve the build with a static HTTP server and run `ui_check.js` with `MOLI_REPORT_SITE`, `MOLI_REPORT_ORIGIN`, and `MOLI_REPORT_SHOTS`. `LEXBENCH_NODE_MODULES` selects the provisioned Playwright installation. The browser pass reviews each top-level page once at the standard desktop width. Renderer tests cover responsive and theme behavior; generation-time tests cover details, filters, dialogs and keyboard navigation. Run the reader pass after resource measurement to avoid contaminating the measurement host.

Publish only after the complete requested method matrix and release checks pass, using the existing bound Artifact Site target and its freshly read remote version. Preserve binary identity, raw trials and previous report versions.

Package the validated tree with `uv run --frozen python scripts/moli_report/pack.py --source /tmp/moli-report-release --output /tmp/moli-report-package`. The packer retains both English entry paths and checks bytes through the bundle manifest; it refuses symlinks, hidden paths, existing destinations and output inside the source tree. Failed packaging leaves no output directory. Its implementation and browser loader live in `harness/lexbench/` and are included in the Python distribution. Run the browser release check against this packaged directory before uploading: serve the package at `MOLI_REPORT_ORIGIN`, while `MOLI_REPORT_SITE` points to the validated unpacked tree that supplies the reference model. The browser checks include the complete resource matrix and frozen measurement-script evidence for the generated editions, plus the English notice when applicable.

## Shared implementation boundary

`harness/lexbench/report.py` owns report assembly; `resource_evidence.py`, `resource_summary.py`, `resource_matrix.py` and `charts.py` own matrix validation, Linux profile aggregation, matrix execution and resource charts. Their command scripts delegate to these modules. Actual measurement executes the declared frozen driver; reading and aggregating saved evidence does not execute it. Historical collectors and call bindings retain their original bytes and hashes.

Moli and Web Fetch publishers use `harness/reporting/packs.py` for deterministic gzip packing and byte offsets. Their adapters retain different entry points and catalogs: Moli serves case evidence through fetch, while Web Fetch packs virtual HTML routes and applies its own privacy projection. The shared packer accepts ordered bytes and neither selects evidence nor changes it. Round-trip tests cover both filename/compression formats, binary and Unicode content, empty files and pack boundaries; publisher tests verify their public catalogs independently.

Moli's scorer bindings, 372-case matrix and source manifests remain in this adapter because they are specific to this benchmark. The renderer is not a second Web Fetch scorer. Common code is extracted where both reports actually use the same operation, rather than introducing a generic renderer with benchmark-specific branches.
