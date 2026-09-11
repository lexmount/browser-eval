UV ?= uv
PY := $(UV) run --frozen python

.PHONY: setup runtime-check test test-python test-ui fixture-smoke ci report report-check

setup:
	$(UV) sync --frozen --all-extras
	npm ci

runtime-check:
	@$(PY) -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'

test-python: runtime-check
	$(PY) -m unittest discover -s tests -v
	$(UV) run --frozen --extra browser-use-eval --extra test python -m pytest tests/browser_use -q

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
	$(UV) run --frozen --extra reference-audit python scripts/check_report_revision.py reports/webfetch
