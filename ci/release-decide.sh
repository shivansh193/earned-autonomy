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
[ -f reports/agent.json ] && agent=(--agent reports/agent.json) || agent=()

ea-risk bundle "${bundle_args[@]}"
ea-risk decide --bundle reports/bundle.json --ledger .trust/ledger.jsonl "${agent[@]}" \
  --out reports/bundle.json --proofcard reports/proofcard.md --dotenv reports/decision.env \
  --audit-url "${CI_JOB_URL:-}/artifacts/file/reports/bundle.json"
cat reports/decision.env
ea-proofcard post --card reports/proofcard.md
