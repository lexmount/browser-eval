#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_HOME="${BROWSER_EVAL_DATA_HOME:-${HOME}/.browser-eval}"
OUTPUT="${BROWSER_USE_OUTPUT_DIR:-${DATA_HOME}/browser-use/current}"
MODEL="${BROWSER_USE_MODEL:-}"

if [[ -z "${MODEL}" ]]; then
  echo "BROWSER_USE_MODEL is required (for example: gpt-6-astra)" >&2
  exit 2
fi

cd "${ROOT}"
uv run --frozen --extra browser-use-eval python -m harness.browser_use.runner \
  --repo-root "${ROOT}" \
  --selection "${ROOT}/evaluation/browser-use/webarena-verified-pilot.json" \
  --output "${OUTPUT}" \
  --model "${MODEL}" \
  "$@"

echo "Evidence: ${OUTPUT}"
