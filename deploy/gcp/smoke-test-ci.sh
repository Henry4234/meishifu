#!/usr/bin/env bash
set -euo pipefail

# 驗證三個 Cloud Run origins 與 Cloudflare 公開路由。
PROJECT_ID="${PROJECT_ID:-meishifu}"
REGION="${REGION:-asia-east1}"
PUBLIC_ORIGIN="${PUBLIC_ORIGIN:-https://meishifu.org}"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"
CURL_BIN="${CURL_BIN:-curl}"

service_url() {
  "${GCLOUD_BIN}" run services describe "$1" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --format='value(status.url)'
}

check_latest_traffic() {
  local service="$1"
  local latest_created
  local latest_ready
  local traffic_revision
  local traffic_percent

  read -r latest_created latest_ready traffic_revision traffic_percent <<< "$(
    "${GCLOUD_BIN}" run services describe "${service}" \
      --project "${PROJECT_ID}" \
      --region "${REGION}" \
      --format='value(status.latestCreatedRevisionName,status.latestReadyRevisionName,status.traffic[0].revisionName,status.traffic[0].percent)'
  )"

  if [[ -z "${latest_created}" || "${latest_created}" != "${latest_ready}" ||
        "${traffic_revision}" != "${latest_ready}" || "${traffic_percent}" != "100" ]]; then
    echo "${service} is not serving its latest ready revision: created=${latest_created:-<empty>} ready=${latest_ready:-<empty>} traffic=${traffic_revision:-<empty>}:${traffic_percent:-<empty>}%" >&2
    return 1
  fi
  echo "Latest revision verified: ${service} -> ${latest_ready} (100%)."
}

check_url() {
  local label="$1"
  local url="$2"

  printf 'Smoke test: %s (%s)\n' "${label}" "${url}"
  "${CURL_BIN}" \
    --fail \
    --silent \
    --show-error \
    --retry 5 \
    --retry-all-errors \
    --retry-delay 1 \
    --connect-timeout 10 \
    --max-time 30 \
    --output /dev/null \
    "${url}"
}

backend_url="$(service_url meishifu-backend)"
frontend_url="$(service_url meishifu-frontend)"
admin_url="$(service_url meishifu-admin)"

check_latest_traffic meishifu-backend
check_latest_traffic meishifu-frontend
check_latest_traffic meishifu-admin

check_url "backend origin" "${backend_url}/api/health"
check_url "frontend origin" "${frontend_url}/health"
check_url "admin origin" "${admin_url}/health"
check_url "public API" "${PUBLIC_ORIGIN}/api/health"
check_url "public website" "${PUBLIC_ORIGIN}/"
check_url "public management" "${PUBLIC_ORIGIN}/management/"

echo "All smoke tests passed."
