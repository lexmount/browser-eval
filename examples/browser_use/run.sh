#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_HOME="${BROWSER_EVAL_DATA_HOME:-${HOME}/.browser-eval}"
OUTPUT="${BROWSER_USE_OUTPUT_DIR:-${DATA_HOME}/browser-use/current}"
if [[ -n "${BROWSER_USE_MODEL:-}" ]]; then
  set -- --model "${BROWSER_USE_MODEL}" "$@"
fi

exec uv run --project "${ROOT}" --frozen --extra browser-use-eval python -m harness.browser_use.runner \
  --repo-root "${ROOT}" \
  --selection "${ROOT}/evaluation/browser-use/webarena-verified-pilot.json" \
  --output "${OUTPUT}" \
  "$@"
