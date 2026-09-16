#!/bin/sh
set -eu

wait_ctrl() {
  url="$1"
  attempts=0
  until wget -qO- "$url/status" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge "${ENV_CTRL_MAX_ATTEMPTS:-360}" ]; then
      echo "env-ctrl timeout: $url" >&2
      exit 1
    fi
    sleep "${ENV_CTRL_RETRY_SECONDS:-5}"
  done
}

init_ctrl() {
  url="$1"
  response="$(wget -qO- --post-data='' "$url/init")"
  echo "$response"
  echo "$response" | grep -Eq '"success"[[:space:]]*:[[:space:]]*true'
}

wait_ctrl "$REDDIT_CTRL_URL"
wait_ctrl "$GITLAB_CTRL_URL"
init_ctrl "$REDDIT_CTRL_URL"
init_ctrl "$GITLAB_CTRL_URL"

mkdir -p /www
printf 'ready\n' >/www/index.html
exec httpd -f -p 8080 -h /www
