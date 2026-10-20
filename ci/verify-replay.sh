#!/usr/bin/env bash
# VERIFY (behaviour): start this commit's app against a fresh Postgres, replay the
# recorded real traffic, and count calls to every vulnerable function the secure
# stage found reachable.
set -euo pipefail
(cd backend && alembic upgrade head && python app/initial_data.py)

targets=$(python - <<'PY'
import json
reach = json.load(open("reports/reach.json"))
print(",".join(sorted({r["resolved_function"] for r in reach
                       if r.get("function") and r.get("reachable") is not False})))
PY
)
export EA_HITS_TOKEN="ci-${CI_JOB_ID:-local}-${RANDOM}"
if [ -n "$targets" ]; then
  export EA_HITS_TARGETS="$targets"
  echo "counting runtime hits for: $targets"
fi

(cd backend && fastapi run app/main.py --host 127.0.0.1 --port 8000 --workers 1 > ../reports/app.log 2>&1) &
for _ in $(seq 1 60); do
  curl -sf http://127.0.0.1:8000/api/v1/utils/health-check/ > /dev/null && break
  sleep 1
done

# Status mismatches make ea-replay exit 1; they are evidence for the decision, not a job failure.
ea-replay run --traffic traffic/recorded.jsonl --target http://127.0.0.1:8000 \
  --login-path /api/v1/login/access-token --username "$EA_USERNAME" \
  --secret SUPERUSER_PASSWORD=env:EA_PASSWORD --out reports/replay.json || true

if [ -n "$targets" ]; then
  curl -sf -H "X-EA-Token: $EA_HITS_TOKEN" http://127.0.0.1:8000/__ea/hits > reports/hits.json
else
  echo '{"hits": {}, "unresolved": {}, "active": false}' > reports/hits.json
fi
cat reports/hits.json; echo
