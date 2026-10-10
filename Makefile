UV ?= uv
PY := $(UV) run --frozen python
TEST_PY := $(UV) run --frozen --all-extras python
WEBMAINBENCH_SAMPLE ?= evaluation/public-webmainbench-200.json
WEBMAINBENCH_PROFILE ?= evaluation/current-report.json
DIAGNOSTIC_PROFILE ?= evaluation/live-webfetch/current-report.json

.PHONY: setup setup-test import-env runtime-check naming-check test test-python test-ui fixture-smoke ci report report-check popular-sites-report popular-sites-report-check wcxb-release-check resource-image public-benchmark-report public-benchmark-sample webmainbench-run webmainbench-score webmainbench-review webmainbench-assemble browser-use-review browser-use-report browser-use-report-check browser-use-visual-check browser-use-release-check trajectory-replay-report trajectory-replay-report-check trajectory-replay-visual-check trajectory-replay-release-check trajectory-replay-package trajectory-replay-publish webarena-catalog-report webarena-catalog-release-check public-webarena-report public-webarena-report-audit public-webarena-report-release

setup:
	$(UV) sync --frozen

setup-test:
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
	@test -n "$(OUTPUT)"
	$(PY) scripts/build_report.py --profile "$(DIAGNOSTIC_PROFILE)" $(if $(EVIDENCE),--evidence "$(EVIDENCE)") --output "$(OUTPUT)"
	$(PY) scripts/check_diagnostic_report.py "$(OUTPUT)" --profile "$(DIAGNOSTIC_PROFILE)"

naming-check: runtime-check
	$(PY) scripts/check_track_naming.py

.PHONY: lint-check
lint-check: runtime-check
	$(TEST_PY) -m ruff check --config 'include=["*.py"]' --select F .
	$(TEST_PY) -m ruff check

test-python: runtime-check
	$(TEST_PY) -m pytest tests -q

.PHONY: test-reference-inputs
test-reference-inputs: runtime-check
	$(TEST_PY) -m pytest tests -q --require-reference-inputs

test-ui:
	node tests/test_report_ui.mjs
	node tests/test_report_loader.mjs
	node tests/browser_use/test_report_ui.mjs

fixture-smoke: runtime-check
	$(PY) -m harness.web_fetch validate --providers fixture
	@tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; $(PY) -m harness.web_fetch run --providers fixture --runs 1 --delay 0 --output "$$tmp/run"

test: test-python test-ui test-native-adapters

ci: naming-check lint-check test fixture-smoke package-check

.PHONY: package-check
package-check: runtime-check
	@set -eu; \
	project_root="$$(pwd)"; \
	package_build="$$(mktemp -d)"; \
	package_run="$$(mktemp -d)"; \
	trap 'rm -rf "$$package_build" "$$package_run"' EXIT; \
	$(UV) build --out-dir "$$package_build"; \
	set -- "$$package_build"/*.whl; \
	test "$$#" -eq 1; test -f "$$1"; \
	wheel="$$1"; \
	cd "$$package_run"; \
	$(UV) run --no-project --isolated --python 3.11 --with "$$wheel" \
	  python "$$project_root/tests/package_smoke.py" --source-root "$$project_root" \
	  --node-html-markdown-path "$$project_root/node_modules/node-html-markdown"; \
	$(UV) run --no-project --isolated --python 3.11 --with "$${wheel}[webmainbench-eval]" \
	  python "$$project_root/tests/package_smoke.py" --source-root "$$project_root" \
	  --node-html-markdown-path "$$project_root/node_modules/node-html-markdown" --webmainbench; \
	$(UV) run --no-project --isolated --python 3.11 --with "$${wheel}[oss-eval]" \
	  python "$$project_root/tests/package_smoke.py" --source-root "$$project_root" \
	  --node-html-markdown-path "$$project_root/node_modules/node-html-markdown" --oss; \
	$(UV) run --no-project --isolated --python 3.11 --with "$${wheel}[reference-audit]" \
	  python "$$project_root/tests/package_smoke.py" --source-root "$$project_root" \
	  --node-html-markdown-path "$$project_root/node_modules/node-html-markdown" --reference-audit

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

.PHONY: webmainbench-audit webmainbench-apply-case-correction
webmainbench-audit: runtime-check
	@test -n "$(EVIDENCE)" -a -n "$(SOURCE)" -a -n "$(OUTPUT)" || (echo 'EVIDENCE, SOURCE and new OUTPUT paths are required' >&2; exit 2)
	$(PY) scripts/audit_webmainbench_suspicions.py --evidence "$(EVIDENCE)" --source-dataset "$(SOURCE)" --output "$(OUTPUT)"

webmainbench-apply-case-correction: runtime-check
	@test -n "$(RUN_ROOT)" -a -n "$(CORRECTION)" -a -n "$(OUTPUT)" -a -n "$(CASE_ID)" -a -n "$(SOURCE)" || (echo 'RUN_ROOT, CORRECTION, OUTPUT, CASE_ID and SOURCE are required' >&2; exit 2)
	$(PY) scripts/apply_webmainbench_case_correction.py --source-root "$(RUN_ROOT)" --correction "$(CORRECTION)" --output "$(OUTPUT)" --case-id "$(CASE_ID)" $(if $(PROVIDERS),--providers "$(PROVIDERS)",) --dataset "$(SOURCE)" --sample-manifest "$(WEBMAINBENCH_SAMPLE)" --profile "$(WEBMAINBENCH_PROFILE)"

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
	@test -n "$(RUN_ROOT)" || (echo 'RUN_ROOT=/absolute/path/to/declared-provider-shards is required' >&2; exit 2)
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	@test -n "$(REVIEW)" || (echo 'REVIEW=/absolute/path/to/new-review.json is required' >&2; exit 2)
	$(UV) run --frozen --extra webmainbench-eval python scripts/review_webmainbench_results.py --source-root "$(RUN_ROOT)" --dataset "$(SOURCE)" --sample-manifest "$(WEBMAINBENCH_SAMPLE)" --profile "$(WEBMAINBENCH_PROFILE)" --output "$(REVIEW)"

webmainbench-assemble: runtime-check
	@test -n "$(RUN_ROOT)" || (echo 'RUN_ROOT=/absolute/path/to/declared-provider-shards is required' >&2; exit 2)
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	@test -n "$(REVIEW)" || (echo 'REVIEW=/absolute/path/to/passed-review.json is required' >&2; exit 2)
	@test -n "$(OUTPUT)" || (echo 'OUTPUT=/absolute/path/to/assembled-evidence is required' >&2; exit 2)
	$(PY) scripts/assemble_webmainbench_evidence.py --source-root "$(RUN_ROOT)" --dataset "$(SOURCE)" --sample-manifest "$(WEBMAINBENCH_SAMPLE)" --profile "$(WEBMAINBENCH_PROFILE)" --review "$(REVIEW)" --output "$(OUTPUT)"

.PHONY: test-native-adapters
test-native-adapters:
	node --test tests/test_public_dns.mjs tests/test_dsh_output.mjs
