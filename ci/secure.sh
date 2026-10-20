#!/usr/bin/env bash
# SECURE: audit locked dependencies, then ask "is the vulnerable function reachable
# from an HTTP route?" For MRs, also audit the target branch so only findings the
# MR introduces are scored.
set -euo pipefail

audit() {  # $1 = project dir, $2 = output prefix
  (cd "$1" && uv export --package app --frozen --no-dev --no-hashes --no-emit-workspace -q) > "reports/$2-requirements.txt"
  # pip-audit exits 1 when it finds vulnerabilities; the JSON is what we need.
  uvx -q "pip-audit==${PIP_AUDIT_VERSION}" -r "reports/$2-requirements.txt" --no-deps --disable-pip \
      -f json -o "reports/$2-audit.json" || test -s "reports/$2-audit.json"
  ea-callgraph findings --pip-audit "reports/$2-audit.json" > "reports/$2-findings.json"
}

audit . head
ea-callgraph batch --app-root backend --app-package app \
  --site-packages .venv/lib/python3.14/site-packages \
  --findings reports/head-findings.json > reports/reach.json

if [ -n "${CI_MERGE_REQUEST_DIFF_BASE_SHA:-}" ]; then
  git worktree add -q /tmp/base "$CI_MERGE_REQUEST_DIFF_BASE_SHA"
  audit /tmp/base base
fi

python - <<'PY'
import json
reach = json.load(open("reports/reach.json"))
print(f"{len(reach)} finding(s)")
for r in reach:
    print(f"  {r.get('cve')} {r.get('package')} {r.get('function')}: reachable={r.get('reachable')}")
PY
