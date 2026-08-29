#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-meishifu}"
REGION="${REGION:-asia-east1}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
REPOSITORY="meishifu"
RUNTIME_SERVICE_ACCOUNT="meishifu-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
TAILSCALE_SECRET="${TAILSCALE_SECRET:-tailscale-auth-key}"
TAILSCALE_DB_HOST="${TAILSCALE_DB_HOST:-100.74.151.0}"
TAILSCALE_DB_PORT="${TAILSCALE_DB_PORT:-3306}"

if [[ -z "${DB_AC:-}" || -z "${DB_PW:-}" ]]; then
  echo "DB_AC and DB_PW must be set in the environment." >&2
  exit 2
fi

GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"

"${GCLOUD_BIN}" config set project "${PROJECT_ID}"
"${GCLOUD_BIN}" config set run/region "${REGION}"

"${GCLOUD_BIN}" services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  storage.googleapis.com

if ! "${GCLOUD_BIN}" artifacts repositories describe "${REPOSITORY}" --location "${REGION}" >/dev/null 2>&1; then
  "${GCLOUD_BIN}" artifacts repositories create "${REPOSITORY}" \
    --repository-format docker \
    --location "${REGION}" \
    --description "Meishifu Cloud Run images"
fi

if ! "${GCLOUD_BIN}" iam service-accounts describe "${RUNTIME_SERVICE_ACCOUNT}" >/dev/null 2>&1; then
  "${GCLOUD_BIN}" iam service-accounts create meishifu-runtime \
    --display-name "Meishifu Cloud Run runtime"
fi

PROJECT_NUMBER="$("${GCLOUD_BIN}" projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
UPLOAD_BUCKET="${UPLOAD_BUCKET:-${PROJECT_ID}-uploads-${PROJECT_NUMBER}}"

if ! "${GCLOUD_BIN}" storage buckets describe "gs://${UPLOAD_BUCKET}" >/dev/null 2>&1; then
  "${GCLOUD_BIN}" storage buckets create "gs://${UPLOAD_BUCKET}" \
    --location "${REGION}" \
    --uniform-bucket-level-access
fi

"${GCLOUD_BIN}" storage buckets add-iam-policy-binding "gs://${UPLOAD_BUCKET}" \
  --member "serviceAccount:${RUNTIME_SERVICE_ACCOUNT}" \
  --role roles/storage.objectAdmin >/dev/null

put_secret() {
  local name="$1"
  local value="$2"
  if ! "${GCLOUD_BIN}" secrets describe "${name}" >/dev/null 2>&1; then
    "${GCLOUD_BIN}" secrets create "${name}" --replication-policy automatic
  fi
  printf %s "${value}" | "${GCLOUD_BIN}" secrets versions add "${name}" --data-file=- >/dev/null
  "${GCLOUD_BIN}" secrets add-iam-policy-binding "${name}" \
    --member "serviceAccount:${RUNTIME_SERVICE_ACCOUNT}" \
    --role roles/secretmanager.secretAccessor >/dev/null
}

ensure_secret() {
  local name="$1"
  local initial_value="$2"
  if ! "${GCLOUD_BIN}" secrets describe "${name}" >/dev/null 2>&1; then
    "${GCLOUD_BIN}" secrets create "${name}" --replication-policy automatic
    printf %s "${initial_value}" | "${GCLOUD_BIN}" secrets versions add "${name}" --data-file=- >/dev/null
  fi
  "${GCLOUD_BIN}" secrets add-iam-policy-binding "${name}" \
    --member "serviceAccount:${RUNTIME_SERVICE_ACCOUNT}" \
    --role roles/secretmanager.secretAccessor >/dev/null
}

put_secret meishifu-db-user "${DB_AC}"
put_secret meishifu-db-password "${DB_PW}"
if [[ -n "${SECRET_KEY:-}" ]]; then
  put_secret meishifu-secret-key "${SECRET_KEY}"
else
  ensure_secret meishifu-secret-key "$(openssl rand -hex 32)"
fi
if [[ -n "${ADMIN_PASSWORD:-}" ]]; then
  put_secret meishifu-admin-password "${ADMIN_PASSWORD}"
else
  ensure_secret meishifu-admin-password "$(openssl rand -base64 24)"
fi

# Tailscale auth key 不應保存在 repository。若環境提供新 key 才新增版本；否則
# 沿用既有 secret，並只確認 Cloud Run runtime identity 具有讀取權限。
if [[ -n "${TAILSCALE_AUTHKEY:-}" ]]; then
  put_secret "${TAILSCALE_SECRET}" "${TAILSCALE_AUTHKEY}"
elif "${GCLOUD_BIN}" secrets describe "${TAILSCALE_SECRET}" >/dev/null 2>&1; then
  "${GCLOUD_BIN}" secrets add-iam-policy-binding "${TAILSCALE_SECRET}" \
    --member "serviceAccount:${RUNTIME_SERVICE_ACCOUNT}" \
    --role roles/secretmanager.secretAccessor >/dev/null
else
  echo "Secret ${TAILSCALE_SECRET} does not exist. Set TAILSCALE_AUTHKEY once before deploying." >&2
  exit 2
fi

"${GCLOUD_BIN}" builds submit . \
  --config deploy/cloudbuild.yaml \
  --substitutions "_REGION=${REGION},_TAG=${TAG}"

IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"

"${GCLOUD_BIN}" run deploy meishifu-backend \
  --image "${IMAGE_BASE}/backend:${TAG}" \
  --service-account "${RUNTIME_SERVICE_ACCOUNT}" \
  --port 8080 \
  --allow-unauthenticated \
  --ingress all \
  --set-env-vars "DB_HOST=127.0.0.1,DB_PORT=13306,DB_NAME=${DB_NAME:-meishifu},DB_POOL_SIZE=${DB_POOL_SIZE:-4},UPLOAD_BUCKET=${UPLOAD_BUCKET},PAY_NOTIFY_URL=https://meishifu.org/api/payment/notify,PAY_RETURN_URL=https://meishifu.org/cart.html,TAILSCALE_ENABLED=true,TAILSCALE_DB_HOST=${TAILSCALE_DB_HOST},TAILSCALE_DB_PORT=${TAILSCALE_DB_PORT},TAILSCALE_HOSTNAME=meishifu-backend" \
  --set-secrets "DB_AC=meishifu-db-user:latest,DB_PW=meishifu-db-password:latest,SECRET_KEY=meishifu-secret-key:latest,TAILSCALE_AUTHKEY=${TAILSCALE_SECRET}:latest"

"${GCLOUD_BIN}" run deploy meishifu-frontend \
  --image "${IMAGE_BASE}/frontend:${TAG}" \
  --port 8080 \
  --allow-unauthenticated \
  --ingress all

"${GCLOUD_BIN}" run deploy meishifu-admin \
  --image "${IMAGE_BASE}/admin:${TAG}" \
  --port 8080 \
  --allow-unauthenticated \
  --ingress all

if [[ "${RUN_DB_INIT:-false}" == "true" ]]; then
  "${GCLOUD_BIN}" run jobs deploy meishifu-db-init \
    --image "${IMAGE_BASE}/backend:${TAG}" \
    --service-account "${RUNTIME_SERVICE_ACCOUNT}" \
    --set-env-vars "DB_HOST=127.0.0.1,DB_PORT=13306,DB_NAME=${DB_NAME:-meishifu},TAILSCALE_ENABLED=true,TAILSCALE_DB_HOST=${TAILSCALE_DB_HOST},TAILSCALE_DB_PORT=${TAILSCALE_DB_PORT},TAILSCALE_HOSTNAME=meishifu-db-init" \
    --set-secrets "DB_AC=meishifu-db-user:latest,DB_PW=meishifu-db-password:latest,DEFAULT_ADMIN_PASSWORD=meishifu-admin-password:latest,TAILSCALE_AUTHKEY=${TAILSCALE_SECRET}:latest" \
    --command /app/backend/start-with-tailscale.sh \
    --args python,init_db.py \
    --max-retries 1 \
    --task-timeout 10m

  "${GCLOUD_BIN}" run jobs execute meishifu-db-init --wait
fi

if find assets/uploads -type f -print -quit | grep -q .; then
  "${GCLOUD_BIN}" storage cp --recursive assets/uploads "gs://${UPLOAD_BUCKET}/"
fi

echo "FRONTEND_ORIGIN=$("${GCLOUD_BIN}" run services describe meishifu-frontend --format='value(status.url)')"
echo "ADMIN_ORIGIN=$("${GCLOUD_BIN}" run services describe meishifu-admin --format='value(status.url)')"
echo "BACKEND_ORIGIN=$("${GCLOUD_BIN}" run services describe meishifu-backend --format='value(status.url)')"
echo "UPLOAD_BUCKET=${UPLOAD_BUCKET}"
