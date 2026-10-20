#!/usr/bin/env bash
# Shared setup for every job: uv, the app's locked environment, our tools.
set -euo pipefail
pip install -q "uv==${UV_VERSION}"
uv sync --package app --frozen -q
uv pip install -q -e tools
mkdir -p reports
