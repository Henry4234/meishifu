#!/usr/bin/env bash
set -euo pipefail

# 一次性建立 GitHub Actions -> GCP 的無金鑰 OIDC 信任與最小部署權限。
PROJECT_ID="${PROJECT_ID:-meishifu}"
REGION="${REGION:-asia-east1}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-Henry4234/meishifu}"
POOL_ID="${POOL_ID:-github-actions}"
PROVIDER_ID="${PROVIDER_ID:-meishifu-main}"
DEPLOYER_NAME="${DEPLOYER_NAME:-github-actions-deployer}"
GCLOUD_BIN="${GCLOUD_BIN:-gcloud}"

DEPLOYER_SA="${DEPLOYER_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="meishifu-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
PROJECT_NUMBER="$("${GCLOUD_BIN}" projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
STATIC_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

"${GCLOUD_BIN}" services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  --project "${PROJECT_ID}"

if ! "${GCLOUD_BIN}" iam service-accounts describe "${DEPLOYER_SA}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  "${GCLOUD_BIN}" iam service-accounts create "${DEPLOYER_NAME}" \
    --project "${PROJECT_ID}" \
    --display-name "GitHub Actions deployer"
fi

DEPLOYER_MEMBER="serviceAccount:${DEPLOYER_SA}"
"${GCLOUD_BIN}" artifacts repositories add-iam-policy-binding meishifu \
  --project "${PROJECT_ID}" \
  --location "${REGION}" \
  --member "${DEPLOYER_MEMBER}" \
  --role roles/artifactregistry.writer >/dev/null

for service in meishifu-backend meishifu-frontend meishifu-admin; do
  "${GCLOUD_BIN}" run services add-iam-policy-binding "${service}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --member "${DEPLOYER_MEMBER}" \
    --role roles/run.developer >/dev/null
done

for service_account in "${RUNTIME_SA}" "${STATIC_SA}"; do
  "${GCLOUD_BIN}" iam service-accounts add-iam-policy-binding "${service_account}" \
    --project "${PROJECT_ID}" \
    --member "${DEPLOYER_MEMBER}" \
    --role roles/iam.serviceAccountUser >/dev/null
done

if ! "${GCLOUD_BIN}" iam workload-identity-pools describe "${POOL_ID}" \
    --project "${PROJECT_ID}" --location global >/dev/null 2>&1; then
  "${GCLOUD_BIN}" iam workload-identity-pools create "${POOL_ID}" \
    --project "${PROJECT_ID}" \
    --location global \
    --display-name "GitHub Actions"
fi

if ! "${GCLOUD_BIN}" iam workload-identity-pools providers describe "${PROVIDER_ID}" \
    --project "${PROJECT_ID}" --location global --workload-identity-pool "${POOL_ID}" >/dev/null 2>&1; then
  "${GCLOUD_BIN}" iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --project "${PROJECT_ID}" \
    --location global \
    --workload-identity-pool "${POOL_ID}" \
    --issuer-uri "https://token.actions.githubusercontent.com/" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository=='${GITHUB_REPOSITORY}' && assertion.ref=='refs/heads/main'"
fi

POOL_NAME="$("${GCLOUD_BIN}" iam workload-identity-pools describe "${POOL_ID}" \
  --project "${PROJECT_ID}" --location global --format='value(name)')"
PROVIDER_NAME="$("${GCLOUD_BIN}" iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --project "${PROJECT_ID}" --location global --workload-identity-pool "${POOL_ID}" \
  --format='value(name)')"
WIF_MEMBER="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository/${GITHUB_REPOSITORY}"

"${GCLOUD_BIN}" iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --project "${PROJECT_ID}" \
  --member "${WIF_MEMBER}" \
  --role roles/iam.workloadIdentityUser >/dev/null

echo "GCP_WORKLOAD_IDENTITY_PROVIDER=${PROVIDER_NAME}"
echo "GCP_SERVICE_ACCOUNT=${DEPLOYER_SA}"
