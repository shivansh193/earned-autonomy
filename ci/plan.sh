#!/bin/sh
# CONFIGURE: terraform plan for an MR that touches infra/. Both services keep their
# current images, so the plan shows only the infrastructure change itself.
# Leaves reports/plan.json for the release stage (ea-tfplan -> risk evidence).
# A plan only reads state, so it takes no lock: a deploy or another MR's plan running at
# the same time must not cost this MR its infrastructure evidence.
set -eu
apk add -q --no-cache curl jq >/dev/null
. ci/gcp-token.sh
mkdir -p reports

current_image() {
  curl -sS -H "Authorization: Bearer ${GCP_ACCESS_TOKEN}" \
    "https://run.googleapis.com/v2/projects/${GCP_PROJECT}/locations/${GCP_REGION}/services/$1" |
    jq -r '.template.containers[0].image // empty'
}
STAGING_IMAGE=$(current_image api-staging)
PROD_IMAGE=$(current_image api-prod)
# Before the first deploy there is nothing to keep, so plan with Google's public
# sample image; the plan then shows the services being created.
PLACEHOLDER=us-docker.pkg.dev/cloudrun/container/hello
STAGING_IMAGE="${STAGING_IMAGE:-$PLACEHOLDER}"
PROD_IMAGE="${PROD_IMAGE:-$PLACEHOLDER}"

cd infra
terraform init -input=false -no-color >/dev/null
terraform plan -input=false -no-color -lock=false -out=plan.out \
  -var "staging_image=${STAGING_IMAGE}" -var "prod_image=${PROD_IMAGE}"
terraform show -json plan.out > ../reports/plan.json
cd ..
echo "plan: $(jq '[.resource_changes[] | .change.actions | join("+")] | group_by(.) | map({(.[0]): length}) | add' reports/plan.json)"
