#!/usr/bin/env bash
# VERIFY (frontend): the template's own Playwright tests, in Chromium, against the real
# backend serving the frontend that frontend-check built (same origin, as in production).
set -euo pipefail
mkdir -p reports
apt-get update -qq && apt-get install -y -qq --no-install-recommends unzip >/dev/null
curl -fsSL https://bun.sh/install | bash >/dev/null
export PATH="$HOME/.bun/bin:$PATH"
bun install --frozen-lockfile
curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh >/dev/null
export PATH="$HOME/.local/bin:$PATH"
uv sync --package app --frozen -q          # fetches the project's Python if the image lacks it
export PATH="$CI_PROJECT_DIR/.venv/bin:$PATH"
(cd backend && alembic upgrade head && python app/initial_data.py)
(cd backend && fastapi run app/main.py --host 127.0.0.1 --port 8000 --workers 1 > ../reports/frontend-app.log 2>&1) &
for _ in $(seq 1 60); do
  curl -sf http://127.0.0.1:8000/api/v1/utils/health-check/ > /dev/null && break
  sleep 1
done
curl -sf http://127.0.0.1:8000/ | grep -q 'id="root"' || { echo "the built frontend is not served at /"; exit 1; }
cd frontend
PLAYWRIGHT_BASE_URL=http://127.0.0.1:8000 PLAYWRIGHT_JUNIT_OUTPUT_NAME=../reports/junit-frontend.xml \
  bunx playwright test --project=chromium --reporter=junit || {
    status=$?
    echo "--- login diagnostic ---"
    PLAYWRIGHT_BASE_URL=http://127.0.0.1:8000 node tests/utils/login-debug.mjs || true
    exit $status
  }
