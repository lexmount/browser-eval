#!/bin/sh
set -eu

socat TCP-LISTEN:9223,bind=0.0.0.0,reuseaddr,fork TCP:127.0.0.1:9222 &
proxy_pid=$!

chromium \
  --headless \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --no-first-run \
  --no-default-browser-check \
  --disable-background-networking \
  --disable-component-update \
  --disable-breakpad \
  --disable-crash-reporter \
  --disable-crashpad \
  --remote-debugging-address=0.0.0.0 \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chromium-profile \
  about:blank &
browser_pid=$!

cleanup() {
  kill "$browser_pid" "$proxy_pid" 2>/dev/null || true
}
trap cleanup EXIT TERM INT

wait "$browser_pid"
