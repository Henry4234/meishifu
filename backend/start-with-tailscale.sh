#!/usr/bin/env bash
set -Eeuo pipefail

prepare_app_command() {
  if [[ "${1:-}" == "serve" ]]; then
    set -- gunicorn \
      --workers "${GUNICORN_WORKERS:-2}" \
      --threads "${GUNICORN_THREADS:-4}" \
      --timeout "${GUNICORN_TIMEOUT:-60}" \
      --bind "0.0.0.0:${PORT:-8080}" \
      --access-logfile - \
      --error-logfile - \
      'app:create_app()'
  fi
  APP_COMMAND=("$@")
}

prepare_app_command "$@"

if [[ "${TAILSCALE_ENABLED:-false}" != "true" ]]; then
  exec "${APP_COMMAND[@]}"
fi

if [[ -z "${TAILSCALE_AUTHKEY:-}" ]]; then
  echo "TAILSCALE_AUTHKEY is required when TAILSCALE_ENABLED=true." >&2
  exit 2
fi

readonly TAILSCALE_SOCKET="${TAILSCALE_SOCKET:-/tmp/tailscaled.sock}"
readonly PROXY_READY_FILE="${TAILSCALE_DB_PROXY_READY_FILE:-/tmp/tailscale-db-proxy.ready}"
readonly AUTH_KEY_FILE="/tmp/tailscale-authkey"
declare -a CHILD_PIDS=()

shutdown() {
  local status="${1:-0}"
  trap - EXIT INT TERM
  if ((${#CHILD_PIDS[@]})); then
    kill "${CHILD_PIDS[@]}" 2>/dev/null || true
    wait "${CHILD_PIDS[@]}" 2>/dev/null || true
  fi
  rm -f "${AUTH_KEY_FILE}" 2>/dev/null || true
  exit "${status}"
}

trap 'shutdown $?' EXIT
trap 'shutdown 130' INT
trap 'shutdown 143' TERM

rm -f "${TAILSCALE_SOCKET}" "${PROXY_READY_FILE}" "${AUTH_KEY_FILE}"

/usr/local/bin/tailscaled \
  --tun=userspace-networking \
  --socks5-server=127.0.0.1:"${TAILSCALE_SOCKS_PORT:-1055}" \
  --state=mem: \
  --socket="${TAILSCALE_SOCKET}" &
tailscaled_pid=$!
CHILD_PIDS+=("${tailscaled_pid}")

for _ in {1..100}; do
  [[ -S "${TAILSCALE_SOCKET}" ]] && break
  if ! kill -0 "${tailscaled_pid}" 2>/dev/null; then
    wait "${tailscaled_pid}"
    exit $?
  fi
  sleep 0.1
done

if [[ ! -S "${TAILSCALE_SOCKET}" ]]; then
  echo "tailscaled did not create ${TAILSCALE_SOCKET} within 10 seconds." >&2
  exit 1
fi

# Avoid exposing the key in process arguments or to application child processes.
umask 077
printf %s "${TAILSCALE_AUTHKEY}" >"${AUTH_KEY_FILE}"
unset TAILSCALE_AUTHKEY

/usr/local/bin/tailscale --socket="${TAILSCALE_SOCKET}" up \
  --auth-key="file:${AUTH_KEY_FILE}" \
  --hostname="${TAILSCALE_HOSTNAME:-meishifu-backend}" \
  --accept-dns=false \
  --accept-routes=false \
  --shields-up \
  --timeout=60s

rm -f "${AUTH_KEY_FILE}"

db_reachable=false
for _ in {1..6}; do
  if python /app/backend/tailscale_db_proxy.py --check; then
    db_reachable=true
    break
  fi
  sleep 2
done

if [[ "${db_reachable}" != "true" ]]; then
  echo "MySQL is not reachable through Tailscale after 6 attempts." >&2
  exit 1
fi

TAILSCALE_DB_PROXY_READY_FILE="${PROXY_READY_FILE}" \
  python /app/backend/tailscale_db_proxy.py &
proxy_pid=$!
CHILD_PIDS+=("${proxy_pid}")

for _ in {1..100}; do
  [[ -f "${PROXY_READY_FILE}" ]] && break
  if ! kill -0 "${proxy_pid}" 2>/dev/null; then
    wait "${proxy_pid}"
    exit $?
  fi
  sleep 0.1
done

if [[ ! -f "${PROXY_READY_FILE}" ]]; then
  echo "Tailscale DB proxy was not ready within 10 seconds." >&2
  exit 1
fi

"${APP_COMMAND[@]}" &
app_pid=$!
CHILD_PIDS+=("${app_pid}")

# Any critical child exit should stop the instance so Cloud Run can replace it.
set +e
wait -n "${CHILD_PIDS[@]}"
status=$?
set -e
exit "${status}"
