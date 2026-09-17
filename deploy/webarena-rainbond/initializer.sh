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
  attempts=0
  while :; do
    set +e
    response="$(wget -qO- --post-data='' "$url/init" 2>&1)"
    result=$?
    set -e
    if echo "$response" | grep -Eq '"success"[[:space:]]*:[[:space:]]*true'; then
      echo "$response"
      return
    fi
    # env-ctrl can restart its managed process before flushing the HTTP response.
    # BusyBox wget reports that successful reset path as an empty/broken response.
    if [ "$result" -ne 0 ] && echo "$response" | grep -Eq 'error getting response|Connection reset'; then
      wait_ctrl "$url"
      echo "env-ctrl init completed after control endpoint restart: $url"
      return
    fi
    attempts=$((attempts + 1))
    if [ "$attempts" -ge "${ENV_CTRL_MAX_ATTEMPTS:-360}" ]; then
      echo "env-ctrl init timeout: $url: $response" >&2
      exit 1
    fi
    echo "env-ctrl init retry: $url" >&2
    sleep "${ENV_CTRL_RETRY_SECONDS:-5}"
  done
}

wait_ctrl "$REDDIT_CTRL_URL"
wait_ctrl "$GITLAB_CTRL_URL"
init_ctrl "$REDDIT_CTRL_URL"
init_ctrl "$GITLAB_CTRL_URL"

mkdir -p /www
printf 'ready\n' >/www/index.html
if busybox --list | grep -qx httpd; then
  exec busybox httpd -f -p 8080 -h /www
fi
if busybox --list | grep -qx nc; then
  while :; do
    printf 'HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\nready\n' |
      busybox nc -l -p 8080
  done
fi
echo "initializer image has neither the httpd nor nc BusyBox applet" >&2
exit 1
