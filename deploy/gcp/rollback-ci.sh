#!/usr/bin/env bash
set -euo pipefail

# CI deployment 失敗時，將三個服務流量切回部署前記錄的 revisions。
PROJECT_ID="${PROJECT_ID:-meishifu}"
REGION="${REGION:-asia-east1}"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"

BACKEND_REVISION="${PREVIOUS_BACKEND_REVISION:-}"
FRONTEND_REVISION="${PREVIOUS_FRONTEND_REVISION:-}"
ADMIN_REVISION="${PREVIOUS_ADMIN_REVISION:-}"

rollback_service() {
  local service="$1"
  local revision="$2"

  if [[ -z "${revision}" || "${revision}" != "${service}-"* ]]; then
    echo "Invalid previous revision for ${service}: ${revision:-<empty>}" >&2
    return 1
  fi

  echo "Rolling back ${service} traffic to ${revision}."
  "${GCLOUD_BIN}" run services update-traffic "${service}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --to-revisions "${revision}=100" \
    --quiet
}

rollback_status=0
rollback_service meishifu-backend "${BACKEND_REVISION}" || rollback_status=1
rollback_service meishifu-frontend "${FRONTEND_REVISION}" || rollback_status=1
rollback_service meishifu-admin "${ADMIN_REVISION}" || rollback_status=1

if [[ "${rollback_status}" -ne 0 ]]; then
  echo "One or more services could not be rolled back." >&2
  exit "${rollback_status}"
fi

echo "Rollback completed for all Cloud Run services."
