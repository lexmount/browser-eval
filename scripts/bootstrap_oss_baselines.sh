#!/usr/bin/env bash
set -euo pipefail

harness_root=$(cd "$(dirname "$0")/.." && pwd)
cowagent_repo=${COWAGENT_REPO:-/private/tmp/cowagent-eval}
openclaw_repo=${OPENCLAW_REPO:-/private/tmp/openclaw-eval}
dsh_repo=${DEEPSEEK_HARNESS_REPO:-/private/tmp/deepseek-harness-eval}
crawl4ai_repo=${CRAWL4AI_REPO:-/private/tmp/crawl4ai-eval}
oss_venv=${OSS_VENV:-/private/tmp/webfetch-oss-venv-py311}
openclaw_node_root=${OPENCLAW_NODE_ROOT:-/private/tmp/openclaw-node}

cowagent_commit=be1782cb3977ee9fa56482d4a5a7d405feb35c8c
openclaw_commit=e0d05367870048cb2f66d611c97b391aadf40c63
dsh_commit=c291e7961a515f6d7af9304e7fd1d257929aef26
crawl4ai_commit=862f6bccb9c063f49b9d42701baa0eea17a4993f

checkout_commit() {
  local url=$1 repo=$2 commit=$3
  if [[ ! -d "$repo/.git" ]]; then
    git clone --no-checkout "$url" "$repo"
  fi
  if [[ $(git -C "$repo" rev-parse HEAD 2>/dev/null || true) != "$commit" ]]; then
    git -C "$repo" fetch --depth 1 origin "$commit"
    git -C "$repo" checkout --detach "$commit"
  fi
  test "$(git -C "$repo" rev-parse HEAD)" = "$commit"
  test -z "$(git -C "$repo" status --porcelain --untracked-files=no)"
}

checkout_commit https://github.com/zhayujie/CowAgent.git "$cowagent_repo" "$cowagent_commit"
checkout_commit https://github.com/openclaw/openclaw.git "$openclaw_repo" "$openclaw_commit"
checkout_commit https://github.com/deepseek-ai/deepseek-harness.git "$dsh_repo" "$dsh_commit"
checkout_commit https://github.com/unclecode/crawl4ai.git "$crawl4ai_repo" "$crawl4ai_commit"

npm install --prefix "$openclaw_node_root" node@24.16.0 tsx@4.23.13

(cd "$openclaw_repo" && PATH="$openclaw_node_root/node_modules/node/bin:$PATH" \
  npx --yes pnpm@12.3.4 install --filter 'openclaw...' --prod --no-optional --frozen-lockfile --ignore-scripts)
(cd "$openclaw_repo" && PATH="$openclaw_node_root/node_modules/node/bin:$PATH" \
  npx --yes pnpm@12.3.4 install --filter @openclaw/normalization-core \
  --filter @openclaw/net-policy --frozen-lockfile --ignore-scripts)
(cd "$openclaw_repo" && PATH="$openclaw_node_root/node_modules/node/bin:$PATH" \
  npx --yes pnpm@12.3.4 --filter @openclaw/normalization-core build)
(cd "$openclaw_repo" && PATH="$openclaw_node_root/node_modules/node/bin:$PATH" \
  npx --yes pnpm@12.3.4 --filter @openclaw/net-policy build)
mkdir -p "$openclaw_repo/node_modules/@openclaw"
ln -sfn ../../packages/normalization-core "$openclaw_repo/node_modules/@openclaw/normalization-core"
ln -sfn ../../packages/net-policy "$openclaw_repo/node_modules/@openclaw/net-policy"
ln -sfn "$openclaw_node_root/node_modules/tsx" "$openclaw_repo/node_modules/tsx"

(cd "$dsh_repo" && npx --yes pnpm@11.7.0 install \
  --filter '@deepseek-ai/cordis...' \
  --filter '@deepseek-ai/dsh-llm...' \
  --filter '@deepseek-ai/dsh-system-prompt...' \
  --filter '@deepseek-ai/dsh-tools...' \
  --filter '@deepseek-ai/dsh-web...' \
  --filter '@deepseek-ai/dsh-web-fetch-http...' \
  --filter '@deepseek-ai/dsh-tool-web...' \
  --filter '@deepseek-ai/dsh-tool-call-timeout-policy...' \
  --filter '@deepseek-ai/dsh-typert-generator...' \
  --frozen-lockfile --ignore-scripts)
(cd "$dsh_repo" && cp "$harness_root/scripts/oss/dsh_target_tsdown.config.ts" .browser-harness-tsdown.config.ts \
  && node_modules/.bin/tsdown --config .browser-harness-tsdown.config.ts)
mkdir -p "$dsh_repo/node_modules/.pnpm/node_modules/@deepseek-ai"
ln -sfn ../../../../packages/web/web-fetch-http "$dsh_repo/node_modules/.pnpm/node_modules/@deepseek-ai/dsh-web-fetch-http"
cp "$harness_root/scripts/oss/dsh_webfetch_worker.mts" "$dsh_repo/node_modules/.pnpm/.browser-harness-dsh-worker.mts"

uv run --frozen python -m venv "$oss_venv"
"$oss_venv/bin/pip" install 'langchain-community==0.3.30'
"$oss_venv/bin/pip" install "$crawl4ai_repo"

printf 'COWAGENT_REPO=%s\nOPENCLAW_REPO=%s\nDEEPSEEK_HARNESS_REPO=%s\nCRAWL4AI_REPO=%s\nOSS_PYTHON=%s/bin/python\nOPENCLAW_NODE=%s/node_modules/node/bin/node\n' "$cowagent_repo" "$openclaw_repo" "$dsh_repo" "$crawl4ai_repo" "$oss_venv" "$openclaw_node_root"
