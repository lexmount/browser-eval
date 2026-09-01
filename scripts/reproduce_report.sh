#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"

output_dir=${1:-runs/reproduced}
env_file=${WEBFETCH_ENV_FILE:-$HOME/.env}
providers=cowagent-built-in,lexmount-global,firecrawl,tavily,exa,cloudflare-markdown

python3 -m browser_harness_eval validate \
  --env-file "$env_file" \
  --providers "$providers"

if [[ "${WEBFETCH_RUN_SMOKE:-0}" == "1" ]]; then
  WEBFETCH_REAL_SMOKE=1 WEBFETCH_SMOKE_ENV_FILE="$env_file" \
    python3 -m unittest tests.test_real_smoke -v
fi

python3 -m browser_harness_eval run \
  --dataset datasets/public_webfetch.json \
  --provider-config configs/providers.example.json \
  --env-file "$env_file" \
  --providers "$providers" \
  --runs "${WEBFETCH_RUNS:-1}" \
  --timeout "${WEBFETCH_TIMEOUT:-45}" \
  --delay "${WEBFETCH_DELAY:-0.5}" \
  --output "$output_dir"

python3 scripts/check_report_links.py "$output_dir/report"
printf 'Report: %s/report/index.html\n' "$output_dir"
