#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export WEBFETCH_REAL_SMOKE=1
export WEBFETCH_SMOKE_ENV_FILE="${WEBFETCH_SMOKE_ENV_FILE:-$PWD/.env}"

python3 -m unittest discover -s tests -p test_real_smoke.py -v
