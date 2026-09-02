#!/usr/bin/env bash
set -euo pipefail

: "${COWAGENT_REPO:?Set COWAGENT_REPO to a CowAgent checkout}"
export WEBFETCH_REAL_SMOKE=1
export WEBFETCH_SMOKE_ENV_FILE="${WEBFETCH_SMOKE_ENV_FILE:-$HOME/.env}"

python3 -m unittest tests.test_real_smoke -v
