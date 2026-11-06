UV ?= uv
PY := $(UV) run --frozen python
TEST_PY := $(UV) run --frozen --all-extras python
PYTEST_ARGS ?=
TEST_PATHS ?= tests
WEBMAINBENCH_SAMPLE ?= evaluation/public-webmainbench-200.json
WEBMAINBENCH_PROFILE ?= $(PROFILE)
REPORT_LANGUAGE ?=
DIAGNOSTIC_PROFILE ?= $(PROFILE)

# Report instances live outside Git, so a required profile has no default.
require_profile = @test -n "$($(or $(1),PROFILE))" || (echo '$(or $(1),PROFILE)=/absolute/path/to/profile.json is required' >&2; exit 2)

.DEFAULT_GOAL := help

.PHONY: setup \
  setup-test \
  help \
  format \
  clean \
  import-env \
  runtime-check \
  report-template \
  webfetch-diagnostic-report \
  track-naming-check \
  naming-check \
  docs-check \
  lint-check \
  test-python \
  test-reference-inputs \
  test-ui \
  fixture-smoke \
  test \
  ci-tests \
  ci \
  shell-check \
  browser-check \
  package-check \
  report \
  report-check \
  popular-sites-report \
  popular-sites-report-check \
  wcxb-release-check \
  browser-use-report \
  browser-use-review \
  browser-use-visual-check \
  browser-use-report-check \
  browser-use-release-check \
  trajectory-replay-report \
  trajectory-replay-report-check \
  trajectory-replay-visual-check \
  trajectory-replay-release-check \
  trajectory-replay-package \
  trajectory-replay-publish \
  webarena-catalog-report \
  webarena-catalog-release-check \
  public-webarena-report \
  public-webarena-report-audit \
  public-webarena-report-release \
  resource-image \
  public-benchmark-report \
  public-benchmark-sample \
  webmainbench-audit \
  webmainbench-apply-case-correction \
  webmainbench-run \
  webmainbench-score \
  webmainbench-review \
  webmainbench-assemble \
  test-native-adapters \
  test-extraction-sdk \
  pin-dataset
setup: ## Install the locked base environment
	$(UV) sync --frozen

setup-test: ## Install every extra, Node packages and benchmark datasets
	$(UV) sync --frozen --all-extras
	npm ci
	$(TEST_PY) -m harness.benchmarks.data

EXTRACTION_SDK_ENV ?= $(or $(BROWSER_EVAL_DATA_HOME),$(HOME)/.browser-eval)/environments/web-extract-sdk-$(shell git hash-object deploy/web-extract/uv.lock)
test-extraction-sdk: ## Test native extraction protocols with the service's locked SDKs
	BROWSER_EVAL_TEST_NATIVE_SDKS=1 LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONPATH=src UV_PROJECT_ENVIRONMENT="$(EXTRACTION_SDK_ENV)" $(UV) run --frozen --project deploy/web-extract --python 3.12 python -m unittest discover -s tests/web_extract -p test_native_sdk_protocol.py

help: ## List documented targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z][a-z0-9-]*:.*## / {printf "  %-36s %s\n", $$1, $$2}' $(firstword $(MAKEFILE_LIST))

format: ## Fix and format selected files: FILES="path.py ..."
	@test -n "$(FILES)" || (echo 'FILES="path.py ..." is required; hash-bound and legacy modules must not be reformatted wholesale' >&2; exit 2)
	$(TEST_PY) -m ruff check --fix $(FILES)
	$(TEST_PY) -m ruff format $(FILES)

clean: ## Remove build outputs and tool caches; keeps reports and environments
	rm -rf build dist src/*.egg-info .pytest_cache .ruff_cache
	find src tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +

import-env: ## Import benchmark credentials into the repository .env
	$(PY) -m harness.web_fetch import-env

runtime-check: ## Require the Python version in .python-version
	@$(PY) -c 'import sys; pinned = open(".python-version").read().strip(); current = "%d.%d" % sys.version_info[:2]; sys.exit(0 if current == pinned else "Python " + pinned + " is required by .python-version; this is Python " + current)'

report-template: runtime-check ## Show a report track renderer and example: TRACK=<track>
	$(PY) scripts/report_template.py "$(TRACK)"

webfetch-diagnostic-report: runtime-check ## Build and check a diagnostic report: DIAGNOSTIC_PROFILE, OUTPUT
	$(call require_profile,DIAGNOSTIC_PROFILE)
	@test -n "$(OUTPUT)"
	$(PY) scripts/build_report.py --profile "$(DIAGNOSTIC_PROFILE)" $(if $(REPORT_LANGUAGE),--language "$(REPORT_LANGUAGE)",) $(if $(EVIDENCE),--evidence "$(EVIDENCE)") --output "$(OUTPUT)"
	$(PY) -m harness.web_fetch diagnostic-check "$(OUTPUT)" --profile "$(DIAGNOSTIC_PROFILE)"

track-naming-check: runtime-check ## Check retired track names
	$(PY) scripts/check_track_naming.py

naming-check: track-naming-check ## Check track names and source language policy
	$(TEST_PY) -m pytest tests/test_source_language_policy.py -q

docs-check: track-naming-check ## Run the documentation-only CI checks
	$(UV) run --frozen --extra test python -m pytest tests/test_readme_navigation.py tests/test_source_language_policy.py tests/test_release_metadata.py::test_project_citations_do_not_claim_a_specific_release -q

lint-check: runtime-check ## Run Ruff
	$(TEST_PY) -m ruff check --config 'include=["*.py"]' --select F,TID252,B023,B905 .
	$(TEST_PY) -m ruff check

test-python: runtime-check ## Run Python tests: TEST_PATHS, PYTEST_ARGS
	$(TEST_PY) -m pytest $(TEST_PATHS) -q $(PYTEST_ARGS)

test-reference-inputs: runtime-check ## Run strict historical-input tests
	$(TEST_PY) -m pytest tests -q --require-reference-inputs

test-ui: ## Run Node report UI tests
	node tests/test_report_ui.mjs
	node tests/test_report_loader.mjs
	node tests/browser_use/test_report_ui.mjs
	node tests/web_extract/test_report_ui.mjs

fixture-smoke: runtime-check ## Run the offline fixture CLI
	$(PY) -m harness.web_fetch validate --providers fixture
	@tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; $(PY) -m harness.web_fetch run --providers fixture --runs 1 --delay 0 --output "$$tmp/run"

test: test-python test-ui test-native-adapters ## Run Python, UI and native adapter tests

ci-tests: track-naming-check lint-check shell-check fixture-smoke ## Run static checks and independent test suites
	$(MAKE) -j2 test test-extraction-sdk

ci: ci-tests package-check browser-check ## Run every CI job: tests, packaging and browser checks

shell-check: ## Run shellcheck on deploy scripts
	@command -v shellcheck >/dev/null || (echo 'shellcheck is required: https://github.com/koalaman/shellcheck#installing' >&2; exit 2)
	find deploy -name '*.sh' -exec shellcheck {} +

browser-check: runtime-check ## Run the CI rendered reference and report checks
	$(TEST_PY) -m playwright install chromium
	BROWSER_EVAL_TEST_BROWSER_BINARY="$$($(TEST_PY) -c 'from playwright.sync_api import sync_playwright; p = sync_playwright().start(); print(p.chromium.executable_path); p.stop()')" \
	  $(TEST_PY) -m pytest tests/test_browser_observation_contracts.py tests/test_report_browser_interactions.py tests/web_search/test_visual.py -q

package-check: runtime-check ## Verify sdist and wheel installation
	$(PY) scripts/check_package.py --uv "$(UV)"

report: runtime-check ## Build the Web Fetch report: PROFILE, EVIDENCE
	$(call require_profile)
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path is required' >&2; exit 2)
	$(PY) scripts/build_report.py --profile "$(PROFILE)" --evidence "$(EVIDENCE)" --output reports/webfetch $(if $(REPORT_LANGUAGE),--language "$(REPORT_LANGUAGE)",) $(if $(filter 1,$(ENGLISH)),--include-english,)

report-check: runtime-check ## Check the Web Fetch report: PROFILE
	$(call require_profile)
	$(PY) scripts/check_report.py reports/webfetch --profile "$(PROFILE)"
	$(PY) scripts/check_report_links.py reports/webfetch
	$(PY) -m harness.web_fetch report-revision-check reports/webfetch

popular-sites-report: runtime-check ## Build the popular-sites report: PROFILE, EVIDENCE
	$(call require_profile)
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path is required' >&2; exit 2)
	$(PY) scripts/build_report.py --profile "$(PROFILE)" --evidence "$(EVIDENCE)" --output reports/popular-sites $(if $(REPORT_LANGUAGE),--language "$(REPORT_LANGUAGE)",) $(if $(filter 1,$(ENGLISH)),--include-english,)

popular-sites-report-check: runtime-check ## Check the popular-sites report: PROFILE
	$(call require_profile)
	$(PY) scripts/check_report.py reports/popular-sites --profile "$(PROFILE)"
	$(PY) scripts/check_report_links.py reports/popular-sites

wcxb-release-check: runtime-check ## Run the WCXB release gate: PROFILE
	$(call require_profile)
	$(PY) scripts/check_report.py reports/wcxb --profile "$(PROFILE)"
	$(PY) scripts/check_report_links.py reports/wcxb
	$(PY) -m harness.web_fetch report-revision-check reports/wcxb
	$(UV) run --frozen --extra headless-eval python -m harness.web_fetch wcxb-visual reports/wcxb

browser-use-report: runtime-check ## Build the Browser Use report: EVIDENCE
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE="/absolute/path [/absolute/path...]" is required' >&2; exit 2)
	$(PY) -m harness.browser_use report $(foreach path,$(EVIDENCE),--evidence "$(path)") --output "$(or $(OUTPUT),reports/browser-use)" $(if $(PROFILE),--profile "$(PROFILE)",) $(if $(REPORT_LANGUAGE),--language "$(REPORT_LANGUAGE)",) $(if $(filter 1,$(ENGLISH)),--include-english,)

browser-use-review: runtime-check ## Review Browser Use results: EVIDENCE
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path is required' >&2; exit 2)
	$(PY) -m harness.browser_use review --evidence "$(EVIDENCE)"

browser-use-visual-check: runtime-check ## Run the Browser Use visual gate
	$(PY) -m harness.browser_use visual "$(or $(OUTPUT),reports/browser-use)" $(if $(VISUAL_RECEIPT),--receipt "$(VISUAL_RECEIPT)",)

browser-use-report-check: runtime-check ## Check the Browser Use report: EVIDENCE
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE="/absolute/path [/absolute/path...]" is required' >&2; exit 2)
	$(PY) -m harness.browser_use check "$(or $(OUTPUT),reports/browser-use)" $(if $(PROFILE),--profile "$(PROFILE)",) $(foreach path,$(EVIDENCE),--evidence "$(path)") --require-complete
	$(PY) scripts/check_report_links.py "$(or $(OUTPUT),reports/browser-use)"

browser-use-release-check: browser-use-report-check ## Run the Browser Use release gate: EVIDENCE
	$(MAKE) browser-use-visual-check

trajectory-replay-report: runtime-check ## Build the Trajectory Replay report: EVIDENCE
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path is required' >&2; exit 2)
	$(PY) -m harness.trajectory_replay report --results "$(EVIDENCE)" --output "$(or $(OUTPUT),reports/trajectory-replay)" $(if $(PROFILE),--profile "$(PROFILE)",) $(if $(REVIEW),--review "$(REVIEW)",) $(if $(REPORT_LANGUAGE),--language "$(REPORT_LANGUAGE)",) $(if $(filter 1,$(ENGLISH)),--include-english,)

trajectory-replay-report-check: runtime-check ## Check the Trajectory Replay report
	$(PY) -m harness.trajectory_replay check "$(or $(OUTPUT),reports/trajectory-replay)" $(if $(PROFILE),--profile "$(PROFILE)",)

trajectory-replay-visual-check: runtime-check ## Run the Trajectory Replay visual gate
	$(PY) -m harness.trajectory_replay visual "$(or $(OUTPUT),reports/trajectory-replay)" $(if $(VISUAL_RECEIPT),--receipt "$(VISUAL_RECEIPT)",)

trajectory-replay-release-check: trajectory-replay-report-check ## Run the Trajectory Replay release gate
	$(MAKE) trajectory-replay-visual-check

trajectory-replay-package: runtime-check ## Package the Trajectory Replay report: EVIDENCE
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path/effective-results.json is required' >&2; exit 2)
	$(PY) -m harness.trajectory_replay package --evidence "$(EVIDENCE)" --report "$(or $(OUTPUT),reports/trajectory-replay)" $(if $(PROFILE),--profile "$(PROFILE)",) $(if $(REVIEW),--review "$(REVIEW)",) $(if $(ARCHIVE),--output "$(ARCHIVE)",) $(if $(ATTESTATION),--attestation "$(ATTESTATION)",) $(if $(VISUAL_RECEIPT),--visual-receipt "$(VISUAL_RECEIPT)",)

trajectory-replay-publish: runtime-check ## Publish the Trajectory Replay report: PROFILE
	$(call require_profile)
	$(PY) -m harness.trajectory_replay publish --profile "$(PROFILE)"

webarena-catalog-report: runtime-check ## Build the WebArena catalog report
	@test -n "$(EVIDENCE_ROOT)" -a -n "$(LEDGER)" -a -n "$(SCOPE)" -a -n "$(CATALOG)" -a -n "$(SUITE)" -a -n "$(SITE_MAP)" -a -n "$(OUTPUT)" -a -n "$(ARCHIVE)" || (echo 'EVIDENCE_ROOT, LEDGER, SCOPE, CATALOG, SUITE, SITE_MAP, OUTPUT and ARCHIVE are required' >&2; exit 2)
	$(PY) -m harness.trajectory_replay catalog-report --evidence-root "$(EVIDENCE_ROOT)" --ledger "$(LEDGER)" --scope "$(SCOPE)" --catalog "$(CATALOG)" --portable-suite "$(SUITE)" --site-map "$(SITE_MAP)" --output "$(OUTPUT)" --archive "$(ARCHIVE)"

webarena-catalog-release-check: runtime-check ## Run the WebArena catalog release gate: PROFILE, COMPARISON_RELEASE
	$(call require_profile)
	$(PY) -m harness.trajectory_replay catalog-release-check --profile "$(PROFILE)" --comparison-release "$(COMPARISON_RELEASE)" $(if $(DATA_HOME),--data-home "$(DATA_HOME)",)

public-webarena-report: runtime-check ## Build the public WebArena report: PROFILE
	$(call require_profile)
	$(PY) scripts/release_webarena_trajectory_report.py --profile "$(PROFILE)" $(if $(FORMAL_ROOT),--formal-root "$(FORMAL_ROOT)",)

public-webarena-report-audit: runtime-check ## Test and build the public WebArena report: PROFILE
	$(call require_profile)
	$(TEST_PY) -m pytest tests/trajectory_replay/public_webarena tests/environments/test_rainbond_slices.py -q
	$(PY) scripts/release_webarena_trajectory_report.py --profile "$(PROFILE)" $(if $(FORMAL_ROOT),--formal-root "$(FORMAL_ROOT)",)

public-webarena-report-release: runtime-check ## Publish the public WebArena report: PROFILE
	$(call require_profile)
	$(PY) scripts/release_webarena_trajectory_report.py --profile "$(PROFILE)" --publish

resource-image: ## Build the Web Fetch resource image
	docker build -f examples/web_fetch/docker/resource.Dockerfile -t lex-web-fetch-resource:local .

public-benchmark-report: runtime-check ## Build and check the public benchmark report: PROFILE, EVIDENCE
	$(call require_profile)
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path/to/reviewed-public-benchmark-evidence is required' >&2; exit 2)
	$(PY) scripts/build_report.py --profile "$(PROFILE)" --evidence "$(EVIDENCE)" --output reports/webfetch-public-benchmarks $(if $(REPORT_LANGUAGE),--language "$(REPORT_LANGUAGE)",) $(if $(filter 1,$(ENGLISH)),--include-english,)
	$(PY) scripts/check_report.py reports/webfetch-public-benchmarks --profile "$(PROFILE)"
	$(PY) scripts/check_report_links.py reports/webfetch-public-benchmarks

public-benchmark-sample: runtime-check ## Select the WebMainBench sample: SOURCE
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	$(PY) -m harness.web_fetch webmainbench-select --source "$(SOURCE)" --output "$(WEBMAINBENCH_SAMPLE)"

webmainbench-audit: runtime-check ## Audit WebMainBench suspicions: EVIDENCE, SOURCE, OUTPUT
	@test -n "$(EVIDENCE)" -a -n "$(SOURCE)" -a -n "$(OUTPUT)" || (echo 'EVIDENCE, SOURCE and new OUTPUT paths are required' >&2; exit 2)
	$(PY) -m harness.web_fetch webmainbench-audit --evidence "$(EVIDENCE)" --source-dataset "$(SOURCE)" --output "$(OUTPUT)"

webmainbench-apply-case-correction: runtime-check ## Apply a case correction: RUN_ROOT, CORRECTION, OUTPUT, CASE_ID, SOURCE
	$(call require_profile,WEBMAINBENCH_PROFILE)
	@test -n "$(RUN_ROOT)" -a -n "$(CORRECTION)" -a -n "$(OUTPUT)" -a -n "$(CASE_ID)" -a -n "$(SOURCE)" || (echo 'RUN_ROOT, CORRECTION, OUTPUT, CASE_ID and SOURCE are required' >&2; exit 2)
	$(PY) -m harness.web_fetch webmainbench-case-correction --source-root "$(RUN_ROOT)" --correction "$(CORRECTION)" --output "$(OUTPUT)" --case-id "$(CASE_ID)" $(if $(PROVIDERS),--providers "$(PROVIDERS)",) --dataset "$(SOURCE)" --sample-manifest "$(WEBMAINBENCH_SAMPLE)" --profile "$(WEBMAINBENCH_PROFILE)"

webmainbench-run: runtime-check ## Run one WebMainBench provider shard: PROVIDER, OUTPUT
	@test -n "$(PROVIDER)" || (echo 'PROVIDER is required' >&2; exit 2)
	@test -n "$(OUTPUT)" || (echo 'OUTPUT=/absolute/path/to/provider-shard is required' >&2; exit 2)
	$(UV) run --frozen --extra headless-eval python scripts/run_webmainbench_benchmark.py --provider "$(PROVIDER)" --case-ids-file "$(WEBMAINBENCH_SAMPLE)" --output "$(OUTPUT)" $(if $(SOURCE),--dataset "$(SOURCE)",)

webmainbench-score: runtime-check ## Score one WebMainBench shard: EVIDENCE
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path/to/provider-shard is required' >&2; exit 2)
	$(UV) run --frozen --extra webmainbench-eval python -m harness.web_fetch webmainbench-score --evidence "$(EVIDENCE)" $(if $(SOURCE),--dataset "$(SOURCE)",) $(if $(SCORER),--scorer-repo "$(SCORER)",) $(if $(DATASET_SHA256),--expected-dataset-sha256 "$(DATASET_SHA256)",) $(if $(REVISION),--revision "$(REVISION)",)

webmainbench-review: runtime-check ## Review WebMainBench results: RUN_ROOT, SOURCE, REVIEW
	$(call require_profile,WEBMAINBENCH_PROFILE)
	@test -n "$(RUN_ROOT)" || (echo 'RUN_ROOT=/absolute/path/to/declared-provider-shards is required' >&2; exit 2)
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	@test -n "$(REVIEW)" || (echo 'REVIEW=/absolute/path/to/new-review.json is required' >&2; exit 2)
	$(UV) run --frozen --extra webmainbench-eval python -m harness.web_fetch webmainbench-review --source-root "$(RUN_ROOT)" --dataset "$(SOURCE)" --sample-manifest "$(WEBMAINBENCH_SAMPLE)" --profile "$(WEBMAINBENCH_PROFILE)" --output "$(REVIEW)"

webmainbench-assemble: runtime-check ## Assemble WebMainBench evidence: RUN_ROOT, SOURCE, REVIEW, OUTPUT
	$(call require_profile,WEBMAINBENCH_PROFILE)
	@test -n "$(RUN_ROOT)" || (echo 'RUN_ROOT=/absolute/path/to/declared-provider-shards is required' >&2; exit 2)
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	@test -n "$(REVIEW)" || (echo 'REVIEW=/absolute/path/to/passed-review.json is required' >&2; exit 2)
	@test -n "$(OUTPUT)" || (echo 'OUTPUT=/absolute/path/to/assembled-evidence is required' >&2; exit 2)
	$(PY) -m harness.web_fetch webmainbench-assemble --source-root "$(RUN_ROOT)" --dataset "$(SOURCE)" --sample-manifest "$(WEBMAINBENCH_SAMPLE)" --profile "$(WEBMAINBENCH_PROFILE)" --review "$(REVIEW)" --output "$(OUTPUT)"

test-native-adapters: ## Run Node native adapter tests
	node --test tests/test_public_dns.mjs tests/test_dsh_output.mjs

pin-dataset: ## Pin a published dataset: DATASET, REV
	@test -n "$(DATASET)" -a -n "$(REV)" || (echo 'DATASET=<track> REV=<full Hugging Face commit> are required' >&2; exit 2)
	$(PY) -m harness.benchmarks.pinning --track "$(DATASET)" --revision "$(REV)"
