#!/usr/bin/env bash
# Shared setup for every job: uv, the app's locked environment, our tools.
set -euo pipefail
pip install -q "uv==${UV_VERSION}"
uv sync --package app --frozen -q
uv pip install -q -e tools
mkdir -p reports
# The app mounts the built SPA from backend/app/frontend (produced by the Docker
# image's bun build). We test and deploy the API only, so an empty directory is enough.
mkdir -p backend/app/frontend
