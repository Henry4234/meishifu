#!/usr/bin/env bash
set -euo pipefail

# GitHub Actions 專用部署：在 GitHub runner 建立 immutable images，推送到
# 單一 Artifact Registry repository，再更新既有 Cloud Run services。
# 基礎設施與 Secret Manager 由 deploy.sh 首次建立，此腳本不讀取或輪替秘密。
PROJECT_ID="${PROJECT_ID:-meishifu}"
PROJECT_NUMBER="${PROJECT_NUMBER:-729707774647}"
REGION="${REGION:-asia-east1}"
TAG="${TAG:-$(git rev-parse HEAD)}"
REPOSITORY="${REPOSITORY:-meishifu}"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"
TAILSCALE_SECRET="${TAILSCALE_SECRET:-tailscale-auth-key}"
TAILSCALE_DB_HOST="${TAILSCALE_DB_HOST:-100.74.151.0}"
TAILSCALE_DB_PORT="${TAILSCALE_DB_PORT:-3306}"

if [[ ! "${PROJECT_NUMBER}" =~ ^[0-9]+$ ]]; then
  echo "PROJECT_NUMBER must contain digits only." >&2
  exit 1
fi

RUNTIME_SERVICE_ACCOUNT="meishifu-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
STATIC_SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
UPLOAD_BUCKET="${UPLOAD_BUCKET:-${PROJECT_ID}-uploads-${PROJECT_NUMBER}}"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"
REGISTRY_HOST="${REGION}-docker.pkg.dev"

"${GCLOUD_BIN}" auth configure-docker "${REGISTRY_HOST}" --quiet

docker build --file frontend/Dockerfile --tag "${IMAGE_BASE}/frontend:${TAG}" .
docker build --file admin/Dockerfile --tag "${IMAGE_BASE}/admin:${TAG}" .
docker build --file backend/Dockerfile --tag "${IMAGE_BASE}/backend:${TAG}" .

docker push "${IMAGE_BASE}/frontend:${TAG}"
docker push "${IMAGE_BASE}/admin:${TAG}"
docker push "${IMAGE_BASE}/backend:${TAG}"

"${GCLOUD_BIN}" run deploy meishifu-backend \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}/backend:${TAG}" \
  --service-account "${RUNTIME_SERVICE_ACCOUNT}" \
  --port 8080 \
  --ingress all \
  --set-env-vars "DB_HOST=127.0.0.1,DB_PORT=13306,DB_NAME=meishifu,DB_POOL_SIZE=4,UPLOAD_BUCKET=${UPLOAD_BUCKET},PAY_NOTIFY_URL=https://meishifu.org/api/payment/notify,PAY_RETURN_URL=https://meishifu.org/cart.html,TAILSCALE_ENABLED=true,TAILSCALE_DB_HOST=${TAILSCALE_DB_HOST},TAILSCALE_DB_PORT=${TAILSCALE_DB_PORT},TAILSCALE_HOSTNAME=meishifu-backend" \
  --set-secrets "DB_AC=meishifu-db-user:latest,DB_PW=meishifu-db-password:latest,SECRET_KEY=meishifu-secret-key:latest,TAILSCALE_AUTHKEY=${TAILSCALE_SECRET}:latest" \
  --quiet

"${GCLOUD_BIN}" run deploy meishifu-frontend \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}/frontend:${TAG}" \
  --service-account "${STATIC_SERVICE_ACCOUNT}" \
  --port 8080 \
  --ingress all \
  --quiet

"${GCLOUD_BIN}" run deploy meishifu-admin \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE_BASE}/admin:${TAG}" \
  --service-account "${STATIC_SERVICE_ACCOUNT}" \
  --port 8080 \
  --ingress all \
  --quiet

echo "Deployed ${TAG} to ${PROJECT_ID}/${REGION}."
