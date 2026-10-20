#!/usr/bin/env bash
# VERIFY (tests): the app's own test suite against a throwaway Postgres, plus our tools' tests.
set -euo pipefail
(cd backend && alembic upgrade head && python app/initial_data.py)
(cd backend && python -m pytest tests -q -p no:cacheprovider --junitxml=../reports/junit.xml)
python -m pytest tools -q -p no:cacheprovider --junitxml=reports/junit-tools.xml
