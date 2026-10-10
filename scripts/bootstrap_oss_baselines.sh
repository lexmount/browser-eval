#!/usr/bin/env bash
set -euo pipefail

harness_root=$(cd "$(dirname "$0")/.." && pwd)
toolchain_root=${WEBFETCH_TOOLCHAIN_ROOT:-$HOME/.browser-eval/toolchains}
openclaw_repo=${OPENCLAW_REPO:-$toolchain_root/openclaw}
dsh_repo=${DEEPSEEK_HARNESS_REPO:-$toolchain_root/deepseek-harness}
cowagent_repo=${COWAGENT_REPO:-$toolchain_root/cowagent}
openclaw_node_root=${OPENCLAW_NODE_ROOT:-$toolchain_root/openclaw-node}
openclaw_rev=${OPENCLAW_REV:-12c4bdf7482f24c85b011e9d858fdec3c245101c}
dsh_rev=${DEEPSEEK_HARNESS_REV:-dd6322d604e00eec1ba5e0c8541159906a21094a}
cowagent_rev=${COWAGENT_REV:-ed5bb344cfe42cee443c8266ba47ee666d8d7471}

mkdir -p "$toolchain_root"

checkout_revision() {
  local url=$1 target=$2 revision=$3
  if [[ ! -d "$target/.git" ]]; then
    mkdir -p "$target"
    git -C "$target" init
    git -C "$target" remote add origin "$url"
  elif [[ "$(git -C "$target" remote get-url origin)" != "$url" ]]; then
    echo "Refusing toolchain checkout with an unexpected origin: $target" >&2
    exit 2
  fi
  if [[ -n "$(git -C "$target" status --porcelain)" ]]; then
    echo "Refusing to replace a dirty toolchain checkout: $target" >&2
    exit 2
  fi
  git -c http.version=HTTP/1.1 -C "$target" fetch --filter=blob:none --depth 1 origin "$revision"
  git -C "$target" checkout --detach "$revision"
  if [[ "$(git -C "$target" config --bool core.sparseCheckout || true)" == "true" ]]; then
    git -C "$target" sparse-checkout disable
  fi
}

checkout_revision https://github.com/openclaw/openclaw.git "$openclaw_repo" "$openclaw_rev"
checkout_revision https://github.com/deepseek-ai/deepseek-harness.git "$dsh_repo" "$dsh_rev"
checkout_revision https://github.com/zhayujie/CowAgent.git "$cowagent_repo" "$cowagent_rev"

npm install --prefix "$openclaw_node_root" node@24.15.0 pnpm@12.1.0
node_bin_dir="$openclaw_node_root/node_modules/node/bin"
openclaw_pnpm="$openclaw_node_root/node_modules/.bin/pnpm"

[[ "$("$node_bin_dir/node" --version)" == "v24.15.0" ]]
[[ "$(PATH="$node_bin_dir:$PATH" "$openclaw_pnpm" --version)" == "12.1.0" ]]

(cd "$openclaw_repo" && PATH="$node_bin_dir:$PATH" "$openclaw_pnpm" install --frozen-lockfile --ignore-scripts)
(cd "$openclaw_repo" && PATH="$node_bin_dir:$PATH" "$openclaw_pnpm" --filter @openclaw/normalization-core build)
(cd "$openclaw_repo" && PATH="$node_bin_dir:$PATH" "$openclaw_pnpm" --filter @openclaw/net-policy build)
mkdir -p "$openclaw_repo/node_modules/@openclaw"
ln -sfn ../../packages/normalization-core "$openclaw_repo/node_modules/@openclaw/normalization-core"
ln -sfn ../../packages/net-policy "$openclaw_repo/node_modules/@openclaw/net-policy"

(cd "$dsh_repo" && PATH="$node_bin_dir:$PATH" corepack pnpm install --frozen-lockfile --ignore-scripts)
# The repository-owned cleaner removes generated output left by another revision
# without broadly deleting unrelated ignored files from the shared checkout.
(cd "$dsh_repo" && PATH="$node_bin_dir:$PATH" corepack pnpm run clean)
(cd "$dsh_repo" && PATH="$node_bin_dir:$PATH" corepack pnpm run build:lib:host)
mkdir -p "$dsh_repo/node_modules/.pnpm/node_modules/@deepseek-ai"
ln -sfn ../../../../packages/web/web-fetch-http "$dsh_repo/node_modules/.pnpm/node_modules/@deepseek-ai/dsh-web-fetch-http"
cp "$harness_root/harness/web_fetch/workers/dsh_webfetch_worker.mts" "$dsh_repo/node_modules/.pnpm/.browser-harness-dsh-worker.mts"
(cd "$dsh_repo" && "$node_bin_dir/node" --import tsx node_modules/.pnpm/.browser-harness-dsh-worker.mts </dev/null)

cd "$harness_root"
uv sync --frozen --all-extras --python 3.11.12
uv run --frozen python -c 'import crawl4ai, docx, langchain_community, openpyxl, pptx'

printf 'COWAGENT_REPO=%s\nOPENCLAW_REPO=%s\nDEEPSEEK_HARNESS_REPO=%s\nOSS_PYTHON=%s/.venv/bin/python\nOPENCLAW_NODE=%s/node_modules/node/bin/node\nOPENCLAW_PNPM=%s\n' "$cowagent_repo" "$openclaw_repo" "$dsh_repo" "$harness_root" "$openclaw_node_root" "$openclaw_pnpm"
