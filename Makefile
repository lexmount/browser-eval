UV ?= uv
PY := $(UV) run --frozen python
TEST_PY := $(UV) run --frozen --all-extras python
WEBMAINBENCH_SAMPLE ?= evaluation/public-webmainbench-200.json

.PHONY: setup runtime-check test test-python test-ui fixture-smoke ci report report-check public-benchmark-report public-benchmark-sample webmainbench-run webmainbench-score webmainbench-review webmainbench-assemble

setup:
	$(UV) sync --frozen --all-extras
	npm ci

runtime-check:
	@$(PY) -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'

test-python: runtime-check
	$(TEST_PY) -m unittest discover -s tests -v
	$(TEST_PY) -m pytest tests/browser_use -q

test-ui:
	node tests/test_report_ui.mjs
	node tests/test_report_loader.mjs

fixture-smoke: runtime-check
	$(PY) -m harness.web_fetch validate --providers fixture
	@tmp=$$(mktemp -d); $(PY) -m harness.web_fetch run --dataset configs/fixture-dataset.json --providers fixture --runs 1 --delay 0 --output "$$tmp/run"

test: test-python test-ui

ci: test fixture-smoke

report: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path is required' >&2; exit 2)
	$(PY) scripts/build_report.py --evidence "$(EVIDENCE)" --output reports/webfetch

report-check: runtime-check
	$(PY) scripts/check_report.py reports/webfetch
	$(PY) scripts/check_report_links.py reports/webfetch
	$(PY) scripts/check_report_revision.py reports/webfetch

public-benchmark-report: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE=/absolute/path/to/reviewed-public-benchmark-evidence is required' >&2; exit 2)
	$(PY) scripts/build_report.py --evidence "$(EVIDENCE)" --output reports/webfetch-public-benchmarks
	$(PY) scripts/check_report.py reports/webfetch-public-benchmarks
	$(PY) scripts/check_report_links.py reports/webfetch-public-benchmarks

public-benchmark-sample: runtime-check
	@test -n "$(SOURCE)" || (echo 'SOURCE=/absolute/path/to/WebMainBench_545.jsonl is required' >&2; exit 2)
	$(PY) scripts/select_public_benchmark_sample.py --source "$(SOURCE)"

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
