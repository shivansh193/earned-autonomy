#!/bin/sh
# Source me: exchange this job's GitLab OIDC token (id_tokens: GITLAB_OIDC_TOKEN) for a
# short-lived Google access token of the deployer service account, via Workload
# Identity Federation. No key files. Needs curl and jq. Exports GCP_ACCESS_TOKEN and
# GOOGLE_OAUTH_ACCESS_TOKEN (read by the Terraform google provider).
set -eu
: "${GITLAB_OIDC_TOKEN:?job needs id_tokens: GITLAB_OIDC_TOKEN}"
: "${GCP_PROJECT_NUMBER:?}" "${GCP_DEPLOYER_SA:?}"

_aud="//iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/gitlab-pool/providers/gitlab"
_fed=$(jq -n --arg aud "$_aud" --arg tok "$GITLAB_OIDC_TOKEN" '{
    grantType: "urn:ietf:params:oauth:grant-type:token-exchange",
    audience: $aud,
    scope: "https://www.googleapis.com/auth/cloud-platform",
    requestedTokenType: "urn:ietf:params:oauth:token-type:access_token",
    subjectToken: $tok,
    subjectTokenType: "urn:ietf:params:oauth:token-type:jwt"}' |
  curl -sSf https://sts.googleapis.com/v1/token -H 'Content-Type: application/json' -d @- | jq -r .access_token)

GCP_ACCESS_TOKEN=$(curl -sSf \
  "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${GCP_DEPLOYER_SA}:generateAccessToken" \
  -H "Authorization: Bearer ${_fed}" -H 'Content-Type: application/json' \
  -d '{"scope": ["https://www.googleapis.com/auth/cloud-platform"], "lifetime": "1800s"}' | jq -r .accessToken)
unset _fed _aud
[ -n "$GCP_ACCESS_TOKEN" ] && [ "$GCP_ACCESS_TOKEN" != "null" ] || { echo "token exchange failed" >&2; exit 1; }
GOOGLE_OAUTH_ACCESS_TOKEN="$GCP_ACCESS_TOKEN"
export GCP_ACCESS_TOKEN GOOGLE_OAUTH_ACCESS_TOKEN
echo "authenticated to Google Cloud as ${GCP_DEPLOYER_SA} (30 min token)"
