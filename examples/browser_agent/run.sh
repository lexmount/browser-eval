#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_HOME="${BROWSER_EVAL_DATA_HOME:-${HOME}/.browser-eval}"
OUTPUT="${BROWSER_AGENT_OUTPUT_DIR:-${DATA_HOME}/browser-agent/current}"

cd "${ROOT}"
uv run --frozen --extra browser-use-eval python -m harness.browser_agent.runner \
  --repo-root "${ROOT}" \
  --selection "${ROOT}/evaluation/browser-agent/webarena-verified-pilot.json" \
  --output "${OUTPUT}" \
  "$@"

echo "Evidence: ${OUTPUT}"
