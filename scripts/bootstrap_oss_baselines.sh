#!/usr/bin/env bash
set -euo pipefail

harness_root=$(cd "$(dirname "$0")/.." && pwd)
openclaw_repo=${OPENCLAW_REPO:-/private/tmp/openclaw-eval}
dsh_repo=${DEEPSEEK_HARNESS_REPO:-/private/tmp/deepseek-harness-eval}
crawl4ai_repo=${CRAWL4AI_REPO:-/private/tmp/crawl4ai-eval}
oss_venv=${OSS_VENV:-/private/tmp/webfetch-oss-venv}
openclaw_node_root=${OPENCLAW_NODE_ROOT:-/private/tmp/openclaw-node}

npm install --prefix "$openclaw_node_root" node@24.15.0

if [[ ! -d "$openclaw_repo/.git" ]]; then
  git clone --depth 1 https://github.com/openclaw/openclaw.git "$openclaw_repo"
fi
(cd "$openclaw_repo" && npx --yes pnpm@12.1.0 install --frozen-lockfile --ignore-scripts)
(cd "$openclaw_repo" && npx --yes pnpm@12.1.0 --filter @openclaw/normalization-core build)
(cd "$openclaw_repo" && npx --yes pnpm@12.1.0 --filter @openclaw/net-policy build)
mkdir -p "$openclaw_repo/node_modules/@openclaw"
ln -sfn ../../packages/normalization-core "$openclaw_repo/node_modules/@openclaw/normalization-core"
ln -sfn ../../packages/net-policy "$openclaw_repo/node_modules/@openclaw/net-policy"

if [[ ! -d "$dsh_repo/.git" ]]; then
  git clone --depth 1 https://github.com/deepseek-ai/deepseek-harness.git "$dsh_repo"
fi
(cd "$dsh_repo" && corepack pnpm install --frozen-lockfile --ignore-scripts)
(cd "$dsh_repo" && corepack pnpm run build:lib:host)
mkdir -p "$dsh_repo/node_modules/.pnpm/node_modules/@deepseek-ai"
ln -sfn ../../../../packages/web/web-fetch-http "$dsh_repo/node_modules/.pnpm/node_modules/@deepseek-ai/dsh-web-fetch-http"
cp "$harness_root/scripts/oss/dsh_webfetch_worker.mts" "$dsh_repo/node_modules/.pnpm/.browser-harness-dsh-worker.mts"

if [[ ! -d "$crawl4ai_repo/.git" ]]; then
  git clone --depth 1 https://github.com/unclecode/crawl4ai.git "$crawl4ai_repo"
fi

python3 -m venv "$oss_venv"
"$oss_venv/bin/pip" install 'langchain-community==0.3.30'
"$oss_venv/bin/pip" install "$crawl4ai_repo"

printf 'OPENCLAW_REPO=%s\nDEEPSEEK_HARNESS_REPO=%s\nCRAWL4AI_REPO=%s\nOSS_PYTHON=%s/bin/python\nOPENCLAW_NODE=%s/node_modules/node/bin/node\n' "$openclaw_repo" "$dsh_repo" "$crawl4ai_repo" "$oss_venv" "$openclaw_node_root"
