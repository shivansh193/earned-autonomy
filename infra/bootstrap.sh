#!/usr/bin/env bash
# One-time Google Cloud setup for Earned Autonomy. Idempotent: safe to re-run.
#
# Creates the long-lived pieces that Terraform builds on, with least privilege:
#   * Artifact Registry repo for app images
#   * GCS bucket for Terraform state
#   * runtime service account (Cloud Run) that can read ONLY the app's secrets
#   * deployer service account (GitLab CI) with Cloud Run admin, image push,
#     state bucket access, and permission to run services as the runtime SA
#   * Workload Identity Federation: GitLab CI jobs from ONE project exchange their
#     OIDC token for the deployer's credentials. No key files exist anywhere.
#
# Secrets (DATABASE_URL per Neon branch, SECRET_KEY, admin password) are created
# separately so their values never pass through this script or the repo.
set -euo pipefail

PROJECT="${PROJECT:-mercurial-lamp-512205-q5}"
REGION="${REGION:-us-central1}"
GITLAB_PROJECT_PATH="${GITLAB_PROJECT_PATH:-gitlab-ai-hackathon/transcend-october-2026/43529136/showcase}"
REPO=earned-autonomy
STATE_BUCKET="${PROJECT}-tfstate"
POOL=gitlab-pool
PROVIDER=gitlab
RUNTIME_SA="ea-runtime@${PROJECT}.iam.gserviceaccount.com"
DEPLOYER_SA="gitlab-deployer@${PROJECT}.iam.gserviceaccount.com"
SECRETS=(ea-database-url-prod ea-database-url-staging ea-secret-key ea-superuser-password)

NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
exists() { "$@" >/dev/null 2>&1; }

exists gcloud artifacts repositories describe "$REPO" --location="$REGION" --project="$PROJECT" ||
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION" \
    --description="Earned Autonomy app images" --project="$PROJECT"

exists gcloud storage buckets describe "gs://${STATE_BUCKET}" ||
  gcloud storage buckets create "gs://${STATE_BUCKET}" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention --project="$PROJECT"
gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning >/dev/null

exists gcloud iam service-accounts describe "$RUNTIME_SA" --project="$PROJECT" ||
  gcloud iam service-accounts create ea-runtime --display-name="Earned Autonomy Cloud Run runtime" --project="$PROJECT"
exists gcloud iam service-accounts describe "$DEPLOYER_SA" --project="$PROJECT" ||
  gcloud iam service-accounts create gitlab-deployer --display-name="GitLab CI deployer (Earned Autonomy)" --project="$PROJECT"

# Deployer: manage Cloud Run services, push images, use the state bucket, act as the runtime SA.
for role in roles/run.admin roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${DEPLOYER_SA}" \
    --role="$role" --condition=None --quiet >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://${STATE_BUCKET}" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/storage.objectAdmin >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --project="$PROJECT" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/iam.serviceAccountUser >/dev/null

# Runtime: read only the app's own secrets (created by the owner, see docs).
for s in "${SECRETS[@]}"; do
  if exists gcloud secrets describe "$s" --project="$PROJECT"; then
    gcloud secrets add-iam-policy-binding "$s" --project="$PROJECT" \
      --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor >/dev/null
  else
    echo "secret $s not created yet; re-run after creating it"
  fi
done

# Workload Identity Federation for GitLab.com CI.
exists gcloud iam workload-identity-pools describe "$POOL" --location=global --project="$PROJECT" ||
  gcloud iam workload-identity-pools create "$POOL" --location=global --project="$PROJECT" \
    --display-name="GitLab CI"
exists gcloud iam workload-identity-pools providers describe "$PROVIDER" --workload-identity-pool="$POOL" --location=global --project="$PROJECT" ||
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --project="$PROJECT" \
    --location=global --workload-identity-pool="$POOL" \
    --issuer-uri="https://gitlab.com" --allowed-audiences="https://gitlab.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.project_path=assertion.project_path,attribute.ref=assertion.ref,attribute.ref_type=assertion.ref_type,attribute.ref_protected=assertion.ref_protected" \
    --attribute-condition="assertion.project_path=='${GITLAB_PROJECT_PATH}'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" --project="$PROJECT" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.project_path/${GITLAB_PROJECT_PATH}" >/dev/null

echo "WIF provider: projects/${NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "deployer:     ${DEPLOYER_SA}"
echo "images:       ${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
echo "tf state:     gs://${STATE_BUCKET}"
