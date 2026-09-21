# Build and verify the Moli report

This directory owns the Moli comparison report renderer. Measured evidence stays outside Git under `BROWSER_EVAL_DATA_HOME` (default: `~/.browser-eval`). It does not use the Web Fetch publication target.

The frozen Linux runner is read from `LEXBENCH_REPORT_RUNNER_ROOT` (default: `$BROWSER_EVAL_DATA_HOME/work/moli-1.1.8-failures-linux`). Its recorded resource-calibration helper verifies the profiler-off and profiler-on pairs. All seven resource configurations use Linux ARM64 release binaries, PSS and process-tree CPU measurements in Docker on the Mac host.

The report profile and chart payload have separate schemas. In `moli_report_profile/1`, resource `tasks` and `attempts_per_task` map to the chart's `total_tasks` and `calls_per_task`; `chart_tasks` and `chart_calls` retain their names. The builder validates the measured populations before constructing the chart payload. Do not pass the profile directly to the plotting API.

Build from validated fixed-layout functional summaries and the complete resource profile directory:

```sh
uv run --frozen python scripts/moli_report/build.py \
  --output /tmp/moli-report-release \
  --fixed-summary 119_off /path/to/patched-off-summary.json \
  --fixed-summary 119_on /path/to/patched-on-summary.json \
  --fixed-summary 119_official_off /path/to/official-off-summary.json \
  --fixed-summary 119_official_on /path/to/official-on-summary.json \
  --linux-resource-profiles /path/to/completed-linux-resource-profiles
uv run --frozen python scripts/moli_report/verify.py --site /tmp/moli-report-release --release
```

Choose a new output directory for each build; the builder does not delete existing output. `--vendor-dir` selects the retained Mermaid distribution and license. Their hashes are checked before copying. The default location is the original report asset directory under the data home; no vendored executable or measured output is committed here.

The four 1.1.9 resource configurations are release builds bound by `resource_builds` in `evaluation/moli/current-report.json`. Functional evidence binds its own binary identity. Do not relabel retained functional outcomes as a new build's measured accuracy.

`uv run --frozen python -m scripts.moli_report.run_linux_resource_profiles --protocol /path/to/frozen-protocol.json --output /path/to/new-evidence-directory` executes the unchanged Lexbench driver and resource collector. Freeze 557 task definitions, fixtures, runner files, four binary hashes, five attempts, jobs=1, the seed and the 250 ms interval before execution. Each configuration has an independent container; all runs execute serially. The official profiler-off calibration precedes the profiler-on measurement. Resource values come from the latter. The builder verifies all 11,140 measured calls and their calibration pairs, checks the frozen 1,045-call chart population, recomputes aggregates and rejects missing or changed evidence. Keep all measured files outside Git.

The static verifier checks score/evidence bindings, resource populations and percentages, fixed-case log links, charts, bilingual rendering, unique resource extrema and negative controls. It invokes the same JavaScript rendering checks used by `tests/test_moli_report_render_contract.py`.

For full release verification, serve the build with a static HTTP server and run `ui_check.js` with `MOLI_REPORT_SITE`, `MOLI_REPORT_ORIGIN`, and `MOLI_REPORT_SHOTS`. `LEXBENCH_NODE_MODULES` selects the provisioned Playwright installation. The browser check covers route classes, themes, widths, evidence dialogs and keyboard navigation. Run it after resource measurement to avoid contaminating the measurement host.

Publish only after the complete requested method matrix and release checks pass, using the existing bound Artifact Site target and its freshly read remote version. Preserve binary identity, raw trials and previous report versions.

Package the validated tree with `uv run --frozen python scripts/moli_report/pack.py --source /tmp/moli-report-release --output /tmp/moli-report-package`. The packer retains both English entry paths and checks bytes through the bundle manifest; it refuses symlinks, hidden paths, and existing destinations. Run the browser release check against this packaged directory before uploading: serve the package at `MOLI_REPORT_ORIGIN`, while `MOLI_REPORT_SITE` points to the validated unpacked tree that supplies the reference model. The browser checks include the complete resource matrix and frozen measurement-script evidence in both languages.

## Shared implementation boundary

Moli and Web Fetch publishers use `harness/reporting/packs.py` for deterministic gzip packing and byte offsets. Their adapters retain different entry points and catalogs: Moli serves case evidence through fetch, while Web Fetch packs virtual HTML routes and applies its own privacy projection. The shared packer accepts ordered bytes and neither selects evidence nor changes it. Round-trip tests cover both filename/compression formats, binary and Unicode content, empty files and pack boundaries; publisher tests verify their public catalogs independently.

Moli's scorer bindings, 372-case matrix and source manifests remain in this adapter because they are specific to this benchmark. The renderer is not a second Web Fetch scorer. Common code is extracted where both reports actually use the same operation, rather than introducing a generic renderer with benchmark-specific branches.
