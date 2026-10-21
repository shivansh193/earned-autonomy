#!/bin/sh
# DEPLOY: roll an image to one environment with Terraform, leaving the other on its
# current image. Usage: ci/deploy.sh staging|prod <image>
# Writes reports/deploy-<env>.env with STAGING_URL / PROD_URL and the image deployed.
set -eu
ENV_NAME="$1"
NEW_IMAGE="$2"
apk add -q --no-cache curl jq >/dev/null
. ci/gcp-token.sh
mkdir -p reports

current_image() {  # $1 = service name; empty if the service doesn't exist yet
  curl -sS -H "Authorization: Bearer ${GCP_ACCESS_TOKEN}" \
    "https://run.googleapis.com/v2/projects/${GCP_PROJECT}/locations/${GCP_REGION}/services/$1" |
    jq -r '.template.containers[0].image // empty'
}

STAGING_IMAGE=$(current_image api-staging)
PROD_IMAGE=$(current_image api-prod)
if [ "$ENV_NAME" = staging ]; then STAGING_IMAGE="$NEW_IMAGE"; else PROD_IMAGE="$NEW_IMAGE"; fi
# First deploy: no service yet, so both start from the new image.
STAGING_IMAGE="${STAGING_IMAGE:-$NEW_IMAGE}"
PROD_IMAGE="${PROD_IMAGE:-$NEW_IMAGE}"
echo "staging -> ${STAGING_IMAGE}"
echo "prod    -> ${PROD_IMAGE}"

cd infra
terraform init -input=false -no-color >/dev/null
# Only touch this environment's resources: prod changes only in the promote job,
# behind the gate the autonomy level chose.
terraform apply -input=false -no-color -auto-approve \
  -target="google_cloud_run_v2_service.api[\"${ENV_NAME}\"]" \
  -target="google_cloud_run_v2_service_iam_member.public[\"${ENV_NAME}\"]" \
  -var "staging_image=${STAGING_IMAGE}" -var "prod_image=${PROD_IMAGE}"
STAGING_URL=$(terraform output -json urls | jq -r '.staging // empty')
PROD_URL=$(terraform output -json urls | jq -r '.prod // empty')
cd ..
printf 'STAGING_URL=%s\nPROD_URL=%s\nDEPLOYED_IMAGE=%s\n' "$STAGING_URL" "$PROD_URL" "$NEW_IMAGE" > "reports/deploy-${ENV_NAME}.env"
cat "reports/deploy-${ENV_NAME}.env"

# Smoke check the environment we just changed.
URL=$([ "$ENV_NAME" = staging ] && echo "$STAGING_URL" || echo "$PROD_URL")
for i in $(seq 1 30); do
  if curl -sf "${URL}/api/v1/utils/health-check/" >/dev/null; then echo "healthy: ${URL}"; exit 0; fi
  sleep 4
done
echo "health check failed: ${URL}" >&2
exit 1
