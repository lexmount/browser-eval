UV ?= uv
PY := $(UV) run --frozen python

.PHONY: setup import-env runtime-check test test-python test-ui fixture-smoke ci report report-check trajectory-report trajectory-report-release trajectory-report-audit

setup:
	$(UV) sync --frozen --all-extras
	npm ci

import-env:
	$(PY) scripts/import_benchmark_env.py

runtime-check:
	@$(PY) -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'

test-python: runtime-check
	$(PY) -m unittest discover -s tests -v
	$(PY) -m pytest tests/browser_use -q

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

trajectory-report: runtime-check
	$(PY) scripts/release_webarena_trajectory_report.py

trajectory-report-release: runtime-check
	$(PY) scripts/release_webarena_trajectory_report.py --publish

trajectory-report-audit: runtime-check
	$(PY) -m pytest tests/browser_use -q
	$(PY) scripts/release_webarena_trajectory_report.py
