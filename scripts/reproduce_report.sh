#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"

output_dir=${1:-runs/reproduced}
env_file=${WEBFETCH_ENV_FILE:-$HOME/.env}
providers=cowagent-built-in,lexmount-global,lexmount-cn,firecrawl,tavily,exa,cloudflare-markdown
resource_providers=cowagent-built-in,lexmount-global,lexmount-cn,firecrawl-resources,tavily-resources,exa-resources,cloudflare-markdown

if [[ "${WEBFETCH_INCLUDE_OSS:-1}" == "1" ]]; then
  export OPENCLAW_REPO=${OPENCLAW_REPO:-/private/tmp/openclaw-eval}
  export DEEPSEEK_HARNESS_REPO=${DEEPSEEK_HARNESS_REPO:-/private/tmp/deepseek-harness-eval}
  export CRAWL4AI_REPO=${CRAWL4AI_REPO:-/private/tmp/crawl4ai-eval}
  export OSS_VENV=${OSS_VENV:-/private/tmp/webfetch-oss-venv}
  export OSS_PYTHON=${OSS_PYTHON:-$OSS_VENV/bin/python}
  export OPENCLAW_NODE_ROOT=${OPENCLAW_NODE_ROOT:-/private/tmp/openclaw-node}
  export OPENCLAW_NODE=${OPENCLAW_NODE:-$OPENCLAW_NODE_ROOT/node_modules/node/bin/node}
  ./scripts/bootstrap_oss_baselines.sh
  providers=$providers,openclaw-built-in,deepseek-harness-built-in,langchain-webbase,crawl4ai
  resource_providers=$resource_providers,openclaw-built-in,deepseek-harness-built-in,langchain-webbase,crawl4ai
fi

python3 -m browser_harness_eval validate \
  --dataset datasets/public_webfetch_100.json \
  --env-file "$env_file" \
  --providers "$providers"

if [[ "${WEBFETCH_RUN_SMOKE:-0}" == "1" ]]; then
  WEBFETCH_REAL_SMOKE=1 WEBFETCH_SMOKE_ENV_FILE="$env_file" \
    python3 -m unittest tests.test_real_smoke -v
fi

python3 -m browser_harness_eval run \
  --dataset datasets/public_webfetch_100.json \
  --provider-config configs/providers.example.json \
  --env-file "$env_file" \
  --providers "$providers" \
  --runs "${WEBFETCH_RUNS:-1}" \
  --timeout "${WEBFETCH_TIMEOUT:-45}" \
  --delay "${WEBFETCH_DELAY:-0.5}" \
  --provider-concurrency "${WEBFETCH_PROVIDER_CONCURRENCY:-4}" \
  --output "$output_dir"

if [[ "${WEBFETCH_RUN_FILE_MEDIA:-1}" == "1" ]]; then
  resource_output=${WEBFETCH_FILE_MEDIA_OUTPUT:-${output_dir}-file-media}
  python3 -m browser_harness_eval run \
    --dataset datasets/file_media_capabilities.json \
    --provider-config configs/providers.example.json \
    --env-file "$env_file" \
    --providers "$resource_providers" \
    --runs "${WEBFETCH_RUNS:-1}" \
    --timeout "${WEBFETCH_TIMEOUT:-45}" \
    --delay "${WEBFETCH_DELAY:-0.5}" \
    --provider-concurrency "${WEBFETCH_PROVIDER_CONCURRENCY:-4}" \
    --output "$resource_output"
  python3 scripts/check_resource_links.py \
    --dataset datasets/file_media_capabilities.json \
    --output "$resource_output/resource-link-checks.json"
  python3 scripts/build_file_media_evidence.py "$resource_output" \
    --link-checks "$resource_output/resource-link-checks.json"
  python3 -m browser_harness_eval report "$output_dir" --dataset datasets/public_webfetch_100.json
fi

python3 scripts/check_report_links.py "$output_dir/report"
printf 'Report: %s/report/index.html\n' "$output_dir"
