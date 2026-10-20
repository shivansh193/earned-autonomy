"""ea-replay: replay recorded traffic against a deployment and report behavioural diffs.

    ea-replay run --traffic traffic/recorded.jsonl --target https://staging.example \\
        --login-path /api/v1/login/access-token \\
        --username "$EA_USERNAME" --password-env EA_PASSWORD \\
        --secret SUPERUSER_PASSWORD=env:EA_PASSWORD \\
        [--baseline https://prod.example] --out replay-report.json

Exit code: 0 = no status mismatches, 1 = mismatches found, 2 = usage error.
Body diffs do not change the exit code; they are evidence for the Validation agent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from replay.model import load
from replay.runner import DEFAULT_UNIQUE, Login, compare, replay


def _secret(spec: str) -> tuple[str, str]:
    name, _, value = spec.partition("=")
    if value.startswith("env:"):
        value = os.environ.get(value[4:], "")
    return name, value


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ea-replay", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="replay traffic and diff")
    r.add_argument("--traffic", type=Path, required=True)
    r.add_argument("--target", required=True)
    r.add_argument("--baseline")
    r.add_argument("--login-path")
    r.add_argument("--username")
    r.add_argument("--password-env", default="EA_PASSWORD")
    r.add_argument("--secret", action="append", default=[], help="NAME=value or NAME=env:VAR")
    r.add_argument("--unique-pattern", default=DEFAULT_UNIQUE)
    r.add_argument("--out", type=Path)
    args = p.parse_args(argv)

    exchanges = load(args.traffic)
    secrets = dict(_secret(s) for s in args.secret)
    login = None
    if args.login_path and args.username:
        login = Login(args.login_path, args.username, os.environ.get(args.password_env, ""))

    def go(url: str):
        return replay(exchanges, url, login=login, secrets=secrets, unique_pattern=args.unique_pattern)

    baseline = go(args.baseline) if args.baseline else None
    candidate = go(args.target)
    report = compare(exchanges, candidate, baseline)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(
        f"replayed {report['requests']} requests over {report['endpoints']} endpoints: "
        f"{report['status_mismatches']} status mismatches, {report['body_diffs']} body diffs, "
        f"p95 {report['p95_ms_before']} -> {report['p95_ms_after']} ms\n"
    )
    return 1 if report["status_mismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
