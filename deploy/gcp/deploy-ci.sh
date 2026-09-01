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
BACKEND_BASE_URL="${BACKEND_BASE_URL:-https://meishifu-backend-729707774647.asia-east1.run.app}"
BACKEND_BASE_URL="${BACKEND_BASE_URL%/}"
FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-https://meishifu.org}"
FRONTEND_BASE_URL="${FRONTEND_BASE_URL%/}"
ECPAY_ENV="${ECPAY_ENV:-production}"
ECPAY_MERCHANT_ID="${ECPAY_MERCHANT_ID:-}"
ECPAY_LOGISTICS_ENV="${ECPAY_LOGISTICS_ENV:-stage}"
# 物流 (C2C 店到店) 的商店代號與金流是兩組不同的帳號。2000933 為綠界 C2C 測試特店。
ECPAY_LOGISTICS_MERCHANT_ID="${ECPAY_LOGISTICS_MERCHANT_ID:-2000933}"
ECPAY_HASH_KEY_SECRET="${ECPAY_HASH_KEY_SECRET:-meishifu-ecpay-hash-key}"
ECPAY_HASH_IV_SECRET="${ECPAY_HASH_IV_SECRET:-meishifu-ecpay-hash-iv}"
PAY_NOTIFY_URL="${PAY_NOTIFY_URL:-${BACKEND_BASE_URL}/api/payment/notify}"
PAY_RESULT_URL="${PAY_RESULT_URL:-${BACKEND_BASE_URL}/api/payment/result}"
PAY_INFO_URL="${PAY_INFO_URL:-${BACKEND_BASE_URL}/api/payment/notify}"
PAY_RETURN_URL="${PAY_RETURN_URL:-${FRONTEND_BASE_URL}/cart.html}"
ECPAY_MAP_REPLY_URL="${ECPAY_MAP_REPLY_URL:-${FRONTEND_BASE_URL}/api/logistics/map-reply}"

if [[ ! "${PROJECT_NUMBER}" =~ ^[0-9]+$ ]]; then
  echo "PROJECT_NUMBER must contain digits only." >&2
  exit 1
fi
if [[ -z "${ECPAY_MERCHANT_ID}" ]]; then
  echo "ECPAY_MERCHANT_ID must be set." >&2
  exit 1
fi
if [[ "${ECPAY_ENV}" != "stage" && "${ECPAY_ENV}" != "production" ]]; then
  echo "ECPAY_ENV must be stage or production." >&2
  exit 1
fi
if [[ "${ECPAY_LOGISTICS_ENV}" != "stage" && "${ECPAY_LOGISTICS_ENV}" != "production" ]]; then
  echo "ECPAY_LOGISTICS_ENV must be stage or production." >&2
  exit 1
fi
# 正式物流環境不可沿用測試特店代號,否則消費者會看到綠界的測試選店地圖。
if [[ "${ECPAY_LOGISTICS_ENV}" == "production" && "${ECPAY_LOGISTICS_MERCHANT_ID}" == "2000933" ]]; then
  echo "ECPAY_LOGISTICS_MERCHANT_ID must be the production C2C merchant id." >&2
  exit 1
fi
if [[ "${BACKEND_BASE_URL}" != https://* || "${FRONTEND_BASE_URL}" != https://* ]]; then
  echo "BACKEND_BASE_URL and FRONTEND_BASE_URL must use HTTPS." >&2
  exit 1
fi

RUNTIME_SERVICE_ACCOUNT="meishifu-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
STATIC_SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
UPLOAD_BUCKET="${UPLOAD_BUCKET:-${PROJECT_ID}-uploads-${PROJECT_NUMBER}}"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"
REGISTRY_HOST="${REGION}-docker.pkg.dev"
BACKEND_ENV_VARS="DB_HOST=127.0.0.1,DB_PORT=13306,DB_NAME=meishifu,DB_POOL_SIZE=4,UPLOAD_BUCKET=${UPLOAD_BUCKET},BACKEND_BASE_URL=${BACKEND_BASE_URL},FRONTEND_BASE_URL=${FRONTEND_BASE_URL},ECPAY_ENV=${ECPAY_ENV},ECPAY_MERCHANT_ID=${ECPAY_MERCHANT_ID},ECPAY_LOGISTICS_ENV=${ECPAY_LOGISTICS_ENV},ECPAY_LOGISTICS_MERCHANT_ID=${ECPAY_LOGISTICS_MERCHANT_ID},PAY_NOTIFY_URL=${PAY_NOTIFY_URL},PAY_RESULT_URL=${PAY_RESULT_URL},PAY_INFO_URL=${PAY_INFO_URL},PAY_RETURN_URL=${PAY_RETURN_URL},ECPAY_MAP_REPLY_URL=${ECPAY_MAP_REPLY_URL},TAILSCALE_ENABLED=true,TAILSCALE_DB_HOST=${TAILSCALE_DB_HOST},TAILSCALE_DB_PORT=${TAILSCALE_DB_PORT},TAILSCALE_HOSTNAME=meishifu-backend"
BACKEND_SECRETS="DB_AC=meishifu-db-user:latest,DB_PW=meishifu-db-password:latest,SECRET_KEY=meishifu-secret-key:latest,ECPAY_HASH_KEY=${ECPAY_HASH_KEY_SECRET}:latest,ECPAY_HASH_IV=${ECPAY_HASH_IV_SECRET}:latest,TAILSCALE_AUTHKEY=${TAILSCALE_SECRET}:latest"

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
  --set-env-vars "${BACKEND_ENV_VARS}" \
  --set-secrets "${BACKEND_SECRETS}" \
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
