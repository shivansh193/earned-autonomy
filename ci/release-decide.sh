#!/usr/bin/env bash
# RELEASE: assemble the evidence bundle, compute risk and the autonomy level, render
# the proof card and post it on the MR.
set -euo pipefail

bundle_args=(--out reports/bundle.json --app-root backend --app-package app
             --junit reports/junit.xml --reachability reports/reach.json
             --hits reports/hits.json --replay reports/replay.json)
if [ -n "${CI_MERGE_REQUEST_DIFF_BASE_SHA:-}" ]; then
  git diff -U0 "$CI_MERGE_REQUEST_DIFF_BASE_SHA" HEAD > reports/change.diff
  bundle_args+=(--diff reports/change.diff)
  [ -f reports/base-findings.json ] && bundle_args+=(--baseline-reachability reports/base-findings.json)
else
  git diff --name-only HEAD~1 HEAD > reports/changed.txt || true
  bundle_args+=(--changed-files reports/changed.txt)
fi
# CONFIGURE evidence: the terraform plan job (once infra/ exists) leaves reports/plan.json.
if [ -f reports/plan.json ]; then
  ea-tfplan reports/plan.json > reports/infra.json
  bundle_args+=(--infra reports/infra.json)
fi
# PLAN evidence: the Planning agent's risk prior on the issue(s) this MR closes
# (scoped label risk-prior::<value>); the highest one applies, capped by the engine.
if [ -n "${CI_MERGE_REQUEST_IID:-}" ]; then
  # The job token makes this work on private projects too (anonymous access only sees public ones).
  PRIOR=$(curl -sf --header "JOB-TOKEN: ${CI_JOB_TOKEN:-}"       "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/merge_requests/${CI_MERGE_REQUEST_IID}/closes_issues" |
    python -c "import json,sys
vals=[float(l.split('::',1)[1]) for i in json.load(sys.stdin) for l in i.get('labels',[]) if l.startswith('risk-prior::')]
print(max(vals) if vals else '')" 2>/dev/null || true)
  if [ -n "$PRIOR" ]; then echo "planning prior from closed issue(s): $PRIOR"; bundle_args+=(--planning-prior "$PRIOR")
  else echo "planning prior: none found (no closed issue with a risk-prior:: label, or the lookup failed)"; fi
fi
[ -f reports/agent.json ] && agent=(--agent reports/agent.json) || agent=()

# GOVERN: decisions use the shared trust ledger (outcomes of past releases) from GCS.
if [ -n "${EA_LEDGER_BUCKET:-}" ] && [ -n "${GITLAB_OIDC_TOKEN:-}" ]; then
  ea-ledger pull --bucket "$EA_LEDGER_BUCKET" --out .trust/ledger.jsonl || echo "ledger pull failed; using the repo copy"
fi
ea-risk state --ledger .trust/ledger.jsonl | python -c "import json,sys; s=json.load(sys.stdin); print('trust:', json.dumps({'thresholds': s['thresholds'], 'area_penalties': s['area_penalties']}))"

ea-risk bundle "${bundle_args[@]}"
ea-risk decide --bundle reports/bundle.json --ledger .trust/ledger.jsonl "${agent[@]}" \
  --out reports/bundle.json --proofcard reports/proofcard.md --dotenv reports/decision.env \
  --audit-url "${CI_JOB_URL:-}/artifacts/file/reports/bundle.json"
cat reports/decision.env
ea-proofcard post --card reports/proofcard.md
