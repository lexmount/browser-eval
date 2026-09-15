UV ?= uv
PY := $(UV) run --frozen python
TEST_PY := $(UV) run --frozen --all-extras python

.PHONY: setup import-env runtime-check naming-check test test-python test-ui fixture-smoke ci report report-check resource-image browser-use-review browser-use-report browser-use-report-check browser-use-visual-check browser-use-release-check trajectory-replay-report trajectory-replay-report-check trajectory-replay-visual-check trajectory-replay-release-check trajectory-replay-package trajectory-replay-publish

setup:
	$(UV) sync --frozen --all-extras
	npm ci

import-env:
	$(PY) scripts/import_benchmark_env.py

runtime-check:
	@$(PY) -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'

naming-check: runtime-check
	$(PY) scripts/check_track_naming.py

test-python: runtime-check
	$(TEST_PY) -m unittest discover -s tests -v
	$(TEST_PY) -m pytest tests/browser_use -q
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
	$(PY) scripts/build_report.py --evidence "$(EVIDENCE)" --output reports/webfetch

report-check: runtime-check
	$(PY) scripts/check_report.py reports/webfetch
	$(PY) scripts/check_report_links.py reports/webfetch
	$(PY) scripts/check_report_revision.py reports/webfetch

browser-use-report: runtime-check
	@test -n "$(EVIDENCE)" || (echo 'EVIDENCE="/absolute/path [/absolute/path...]" is required' >&2; exit 2)
	$(PY) scripts/build_browser_use_report.py $(foreach path,$(EVIDENCE),--evidence "$(path)") --output reports/browser-use

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
	$(PY) scripts/build_webarena_replay_report.py --results "$(EVIDENCE)"

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

resource-image:
	docker build -f examples/web_fetch/docker/resource.Dockerfile -t lex-web-fetch-resource:local .
