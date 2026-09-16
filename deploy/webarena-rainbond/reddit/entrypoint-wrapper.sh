#!/bin/sh
set -eu

case "${WA_ENV_CTRL_EXTERNAL_SITE_URL:-}" in
  http://*|https://*) ;;
  *)
    echo "WA_ENV_CTRL_EXTERNAL_SITE_URL must be an absolute HTTP URL" >&2
    exit 64
    ;;
esac

"${WA_ORIGINAL_ENTRYPOINT}" "$@" &
child_pid=$!

terminate() {
  kill -TERM "${child_pid}" 2>/dev/null || true
  wait "${child_pid}" || true
}
trap terminate INT TERM

control_url="http://127.0.0.1:${WA_ENV_CTRL_PORT:-8877}/status"
attempt=0
until curl --fail --silent --show-error "${control_url}" >/dev/null 2>&1; do
  if ! kill -0 "${child_pid}" 2>/dev/null; then
    wait "${child_pid}"
    exit $?
  fi
  attempt=$((attempt + 1))
  if [ "${attempt}" -ge 360 ]; then
    echo "env-ctrl did not become ready within 30 minutes" >&2
    terminate
    exit 1
  fi
  sleep 5
done

attempt=0
until env-ctrl --verbose init --base-url "${WA_ENV_CTRL_EXTERNAL_SITE_URL}"; do
  if ! kill -0 "${child_pid}" 2>/dev/null; then
    wait "${child_pid}"
    exit $?
  fi
  attempt=$((attempt + 1))
  if [ "${attempt}" -ge 60 ]; then
    echo "env-ctrl init failed after 60 attempts" >&2
    terminate
    exit 1
  fi
  sleep 5
done

touch /tmp/rainbond-env-ctrl-init-ok
wait "${child_pid}"
