UV ?= uv
PY := $(UV) run --frozen python
TEST_PY := $(UV) run --frozen --all-extras python
WEBMAINBENCH_SAMPLE ?= evaluation/public-webmainbench-200.json

.PHONY: setup import-env runtime-check naming-check test test-python test-ui fixture-smoke ci report report-check popular-sites-report popular-sites-report-check wcxb-release-check resource-image public-benchmark-report public-benchmark-sample webmainbench-run webmainbench-score webmainbench-review webmainbench-assemble browser-use-review browser-use-report browser-use-report-check browser-use-visual-check browser-use-release-check trajectory-replay-report trajectory-replay-report-check trajectory-replay-visual-check trajectory-replay-release-check trajectory-replay-package trajectory-replay-publish webarena-catalog-report webarena-catalog-release-check public-webarena-report public-webarena-report-audit public-webarena-report-release

setup:
	$(UV) sync --frozen --all-extras
	npm ci

import-env:
	$(PY) scripts/import_benchmark_env.py

runtime-check:
	@$(PY) -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'

.PHONY: report-template webfetch-diagnostic-report
report-template: runtime-check
	$(PY) scripts/report_template.py "$(TRACK)"

webfetch-diagnostic-report: runtime-check
	@test -n "$(EVIDENCE)" -a -n "$(OUTPUT)"
	$(PY) scripts/build_report.py --diagnostic-cohorts --evidence "$(EVIDENCE)" --output "$(OUTPUT)"
	$(PY) scripts/check_diagnostic_report.py "$(OUTPUT)"

naming-check: runtime-check
	$(PY) scripts/check_track_naming.py

test-python: runtime-check
	$(TEST_PY) -m unittest discover -s tests -v
	$(TEST_PY) -m pytest tests/test_local_audit.py tests/test_dynamic_100_moli_119.py tests/test_three_cohort_review_followup.py tests/test_training_list_contract.py tests/test_risk_control_dataset.py tests/test_composite_tasks.py tests/test_risk_control_report.py -q
	$(TEST_PY) -m pytest tests/browser_use -q
	$(TEST_PY) -m pytest tests/environments tests/test_manage_webarena_rainbond_pool.py -q
	$(TEST_PY) -m pytest tests/trajectory_replay -q

test-ui:
	node tests/test_report_ui.mjs
	node tests/test_report_loader.mjs
	node tests/browser_use/test_report_ui.mjs

fixture-smoke: runtime-check
	$(PY) -m harness.web_fetch validate --providers fixture
	@tmp=$$(mktemp -d); $(PY) -m harness.web_fetch run --dataset configs/fixture-dataset.json --providers fixture --runs 1 --delay 0 --output "$$tmp/run"

test: test-python test-ui

ci: naming-check test fixture-smoke

report: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path is required' >&2; exit 2)
	$(PY) scripts/build_report.py --evidence "$(EVIDENCE)" --output reports/webfetch $(if $(filter 1,$(ENGLISH)),--include-english,)

report-check: runtime-check
	$(PY) scripts/check_report.py reports/webfetch
	$(PY) scripts/check_report_links.py reports/webfetch
	$(PY) scripts/check_report_revision.py reports/webfetch

popular-sites-report: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path is required' >&2; exit 2)
	$(PY) scripts/build_report.py --profile evaluation/popular-sites/current-report.json --evidence "$(EVIDENCE)" --output reports/popular-sites $(if $(filter 1,$(ENGLISH)),--include-english,)

popular-sites-report-check: runtime-check
	$(PY) scripts/check_report.py reports/popular-sites --profile evaluation/popular-sites/current-report.json
	$(PY) scripts/check_report_links.py reports/popular-sites

wcxb-release-check: runtime-check
	$(PY) scripts/check_report.py reports/wcxb
	$(PY) scripts/check_report_links.py reports/wcxb
	$(PY) scripts/check_report_revision.py reports/wcxb
	$(UV) run --frozen --extra headless-eval python scripts/check_wcxb_visual.py reports/wcxb

browser-use-report: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE="/absolute/path [/absolute/path...]" is required' >&2; exit 2)
	$(PY) scripts/build_browser_use_report.py $(foreach path,$(EVIDENCE),--evidence "$(path)") --output reports/browser-use $(if $(filter 1,$(ENGLISH)),--include-english,)

browser-use-review: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path is required' >&2; exit 2)
	$(PY) scripts/review_browser_use_results.py --evidence "$(EVIDENCE)"

browser-use-visual-check: runtime-check
	$(PY) scripts/check_browser_use_visual.py reports/browser-use

browser-use-report-check: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE="/absolute/path [/absolute/path...]" is required' >&2; exit 2)
	$(PY) scripts/check_browser_use_report.py reports/browser-use $(foreach path,$(EVIDENCE),--evidence "$(path)") --require-complete
	$(PY) scripts/check_report_links.py reports/browser-use

browser-use-release-check: browser-use-report-check
	$(MAKE) browser-use-visual-check

trajectory-replay-report: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path is required' >&2; exit 2)
	$(PY) scripts/build_webarena_replay_report.py --results "$(EVIDENCE)" $(if $(filter 1,$(ENGLISH)),--include-english,)

trajectory-replay-report-check: runtime-check
	$(PY) scripts/check_webarena_replay_report.py reports/trajectory-replay

trajectory-replay-visual-check: runtime-check
	$(PY) scripts/check_webarena_replay_visual.py reports/trajectory-replay

trajectory-replay-release-check: trajectory-replay-report-check
	$(MAKE) trajectory-replay-visual-check

trajectory-replay-package: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path/effective-results.json is required' >&2; exit 2)
	$(PY) scripts/package_webarena_replay_artifact_site.py --evidence "$(EVIDENCE)"

trajectory-replay-publish: runtime-check
	$(PY) scripts/publish_webarena_replay_artifact_site.py

webarena-catalog-report: runtime-check
	@test -n "$(EVIDENCE_ROOT)" -a -n "$(LEDGER)" -a -n "$(SCOPE)" -a -n "$(CATALOG)" -a -n "$(SUITE)" -a -n "$(SITE_MAP)" -a -n "$(OUTPUT)" -a -n "$(ARCHIVE)" || (echo 'EVIDENCE_ROOT, LEDGER, SCOPE, CATALOG, SUITE, SITE_MAP, OUTPUT and ARCHIVE are required' >&2; exit 2)
	$(PY) scripts/build_webarena_catalog_report.py --evidence-root "$(EVIDENCE_ROOT)" --ledger "$(LEDGER)" --scope "$(SCOPE)" --catalog "$(CATALOG)" --portable-suite "$(SUITE)" --site-map "$(SITE_MAP)" --output "$(OUTPUT)" --archive "$(ARCHIVE)"

webarena-catalog-release-check: runtime-check
	$(PY) scripts/check_webarena_catalog_release.py $(if $(DATA_HOME),--data-home "$(DATA_HOME)",)

public-webarena-report: runtime-check
	$(PY) scripts/release_webarena_trajectory_report.py $(if $(FORMAL_ROOT),--formal-root "$(FORMAL_ROOT)",)

public-webarena-report-audit: runtime-check
	$(TEST_PY) -m pytest tests/trajectory_replay/public_webarena -q
	$(PY) scripts/release_webarena_trajectory_report.py $(if $(FORMAL_ROOT),--formal-root "$(FORMAL_ROOT)",)

public-webarena-report-release: runtime-check
	$(PY) scripts/release_webarena_trajectory_report.py --publish

resource-image:
	docker build -f examples/web_fetch/docker/resource.Dockerfile -t lex-web-fetch-resource:local .

public-benchmark-report: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path/to/reviewed-public-benchmark-evidence is required' >&2; exit 2)
	$(PY) scripts/build_report.py --evidence "$(EVIDENCE)" --output reports/webfetch-public-benchmarks $(if $(filter 1,$(ENGLISH)),--include-english,)
	$(PY) scripts/check_report.py reports/webfetch-public-benchmarks
	$(PY) scripts/check_report_links.py reports/webfetch-public-benchmarks

public-benchmark-sample: runtime-check
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	$(PY) scripts/select_public_benchmark_sample.py --source "$(SOURCE)"

.PHONY: webmainbench-audit webmainbench-navigation-diagnostic webmainbench-kitesurf-diagnostic webmainbench-apply-case-correction
webmainbench-audit: runtime-check
	@test -n "$(EVIDENCE)" -a -n "$(SOURCE)" -a -n "$(OUTPUT)" || (echo 'EVIDENCE, SOURCE and new OUTPUT paths are required' >&2; exit 2)
	$(PY) scripts/audit_webmainbench_suspicions.py --evidence "$(EVIDENCE)" --source-dataset "$(SOURCE)" --output "$(OUTPUT)"

webmainbench-apply-case-correction: runtime-check
	@test -n "$(RUN_ROOT)" -a -n "$(CORRECTION)" -a -n "$(OUTPUT)" -a -n "$(CASE_ID)" -a -n "$(SOURCE)" || (echo 'RUN_ROOT, CORRECTION, OUTPUT, CASE_ID and SOURCE are required' >&2; exit 2)
	$(PY) scripts/apply_webmainbench_case_correction.py --source-root "$(RUN_ROOT)" --correction "$(CORRECTION)" --output "$(OUTPUT)" --case-id "$(CASE_ID)" $(if $(PROVIDERS),--providers "$(PROVIDERS)",) --dataset "$(SOURCE)" --sample-manifest "$(WEBMAINBENCH_SAMPLE)"

# Diagnostic controls deliberately mutate test inputs and never enter a report cohort.
webmainbench-navigation-diagnostic: runtime-check
	@test -n "$(SOURCE)" -a -n "$(BINARY)" -a -n "$(OUTPUT)" || (echo 'SOURCE, BINARY and new OUTPUT paths are required' >&2; exit 2)
	$(PY) scripts/diagnose_webmainbench_navigation.py --dataset "$(SOURCE)" --binary "$(BINARY)" --output "$(OUTPUT)"

# Diagnostic input ablations are isolated from the score-bearing cohort.
webmainbench-kitesurf-diagnostic: runtime-check
	@test -n "$(SOURCE)" -a -n "$(OUTPUT)" || (echo 'SOURCE and new OUTPUT paths are required' >&2; exit 2)
	$(PY) scripts/diagnose_webmainbench_kitesurf.py --dataset "$(SOURCE)" --output "$(OUTPUT)" $(if $(PROFILE),--profile "$(PROFILE)",) $(if $(RESUME),--resume,)

webmainbench-run: runtime-check
	@test -n "$(PROVIDER)" || (echo 'PROVIDER is required' >&2; exit 2)
	@test -n "$(OUTPUT)" || (echo 'OUTPUT=/absolute/path/to/provider-shard is required' >&2; exit 2)
	$(UV) run --frozen --extra headless-eval python scripts/run_webmainbench_benchmark.py --provider "$(PROVIDER)" --case-ids-file "$(WEBMAINBENCH_SAMPLE)" --output "$(OUTPUT)"

webmainbench-score: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path/to/provider-shard is required' >&2; exit 2)
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	@test -n "$(SCORER)" || (echo 'SCORER=/absolute/path/to/pinned/WebMainBench is required' >&2; exit 2)
	$(UV) run --frozen --extra webmainbench-eval python scripts/score_webmainbench_results.py --evidence "$(EVIDENCE)" --dataset "$(SOURCE)" --scorer-repo "$(SCORER)"

webmainbench-review: runtime-check
	@test -n "$(RUN_ROOT)" || (echo 'RUN_ROOT=/absolute/path/to/five-provider-shards is required' >&2; exit 2)
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	@test -n "$(REVIEW)" || (echo 'REVIEW=/absolute/path/to/new-review.json is required' >&2; exit 2)
	$(UV) run --frozen --extra webmainbench-eval python scripts/review_webmainbench_results.py --source-root "$(RUN_ROOT)" --dataset "$(SOURCE)" --sample-manifest "$(WEBMAINBENCH_SAMPLE)" --output "$(REVIEW)"

webmainbench-assemble: runtime-check
	@test -n "$(RUN_ROOT)" || (echo 'RUN_ROOT=/absolute/path/to/five-provider-shards is required' >&2; exit 2)
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	@test -n "$(REVIEW)" || (echo 'REVIEW=/absolute/path/to/passed-review.json is required' >&2; exit 2)
	@test -n "$(OUTPUT)" || (echo 'OUTPUT=/absolute/path/to/assembled-evidence is required' >&2; exit 2)
	$(PY) scripts/assemble_webmainbench_evidence.py --source-root "$(RUN_ROOT)" --dataset "$(SOURCE)" --sample-manifest "$(WEBMAINBENCH_SAMPLE)" --review "$(REVIEW)" --output "$(OUTPUT)"
