"""ea-risk: build the evidence bundle, score it, record the decision, render the proof card.

    ea-risk bundle  --out bundle.json --changed-files changed.txt --junit report.xml \\
                    --reachability reach.json --hits hits.json --replay replay.json \\
                    [--verdicts verdicts.json] [--infra tfplan.json] [--app-root backend --app-package app]
    ea-risk decide  --bundle bundle.json --ledger .trust/ledger.jsonl [--agent agent.json] \\
                    --out bundle.json [--proofcard proofcard.md] [--record]
    ea-risk outcome --ledger .trust/ledger.jsonl --decision-id ID --result rollback|incident|replay_failed|clean
    ea-risk state   --ledger .trust/ledger.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from proofcard import render
from risk import ledger
from risk.bundle import (
    build_bundle,
    load_json,
    parse_diff,
    replay_summary,
    routes_touched,
    security_findings,
    junit_summary,
)
from risk.engine import decide


def _write(path: Path | None, data: object) -> None:
    text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def cmd_bundle(a: argparse.Namespace) -> int:
    changed = [l.strip() for l in a.changed_files.read_text(encoding="utf-8").splitlines() if l.strip()] if a.changed_files else []
    diff = parse_diff(a.diff.read_text(encoding="utf-8")) if a.diff else None
    if diff is not None and not changed:
        changed = sorted(diff)
    routes: list[str] = []
    if a.app_root and changed:
        from callgraph.index import build_index

        idx = build_index(a.app_root, a.app_package or ["app"], defines=dict(d.split("=", 1) for d in a.define))
        routes = routes_touched(idx, changed, a.repo_root, diff)
    bundle = build_bundle(
        mr_iid=a.mr_iid or _int_env("CI_MERGE_REQUEST_IID"),
        commit=a.commit or os.environ.get("CI_COMMIT_SHA"),
        pipeline_id=a.pipeline_id or _int_env("CI_PIPELINE_ID"),
        changed_files=changed,
        routes=routes,
        tests=junit_summary(a.junit) if a.junit and a.junit.exists() else None,
        security=security_findings(load_json(a.reachability), load_json(a.hits), load_json(a.baseline_reachability)),
        replay=replay_summary(load_json(a.replay), load_json(a.verdicts)),
        infra=load_json(a.infra),
        planning_prior=a.planning_prior,
    )
    _write(a.out, bundle)
    return 0


def _int_env(name: str) -> int | None:
    v = os.environ.get(name)
    return int(v) if v and v.isdigit() else None


def cmd_decide(a: argparse.Namespace) -> int:
    bundle = json.loads(a.bundle.read_text(encoding="utf-8"))
    st = ledger.state(ledger.load(a.ledger))
    agent = load_json(a.agent)
    result = decide(bundle, st["thresholds"], st["area_penalties"], agent)
    bundle.update(result)
    if a.record:
        entry = ledger.append(a.ledger, ledger.decision_entry(bundle, result))
        bundle["provenance"]["decision_id"] = entry["id"]
    if a.proofcard:
        card = render(bundle, audit_url=a.audit_url)
        _write(a.proofcard, card)
        # Agents read artifacts through a JSON-only API tool, so the card travels in the bundle too.
        bundle["proofcard_markdown"] = card
    _write(a.out, bundle)
    sys.stderr.write(f"level={result['autonomy']['level']} risk={result['risk']['final']}\n")
    if a.dotenv:  # for GitLab CI `artifacts:reports:dotenv`
        a.dotenv.write_text(
            f"EA_LEVEL={result['autonomy']['level']}\nEA_RISK={result['risk']['final']}\n"
            f"EA_DECISION_ID={bundle['provenance'].get('decision_id', '')}\n", encoding="utf-8")
    return 0


def cmd_outcome(a: argparse.Namespace) -> int:
    ledger.append(a.ledger, ledger.outcome_entry(a.decision_id, a.result, a.detail, a.seconds))
    _write(None, ledger.state(ledger.load(a.ledger)))
    return 0


def cmd_state(a: argparse.Namespace) -> int:
    _write(None, ledger.state(ledger.load(a.ledger)))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ea-risk", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bundle")
    b.add_argument("--out", type=Path)
    b.add_argument("--mr-iid", type=int)
    b.add_argument("--commit")
    b.add_argument("--pipeline-id", type=int)
    b.add_argument("--changed-files", type=Path)
    b.add_argument("--diff", type=Path, help="git diff -U0 output: function-level route scope")
    b.add_argument("--baseline-reachability", type=Path, help="target branch findings: only new ones are scored")
    b.add_argument("--repo-root", type=Path, default=Path("."))
    b.add_argument("--app-root", type=Path)
    b.add_argument("--app-package", action="append")
    b.add_argument("--define", action="append", default=[])
    b.add_argument("--junit", type=Path)
    b.add_argument("--reachability", type=Path)
    b.add_argument("--hits", type=Path)
    b.add_argument("--replay", type=Path)
    b.add_argument("--verdicts", type=Path)
    b.add_argument("--infra", type=Path)
    b.add_argument("--planning-prior", type=float)
    b.set_defaults(fn=cmd_bundle)

    d = sub.add_parser("decide")
    d.add_argument("--bundle", type=Path, required=True)
    d.add_argument("--ledger", type=Path, required=True)
    d.add_argument("--agent", type=Path)
    d.add_argument("--out", type=Path)
    d.add_argument("--proofcard", type=Path)
    d.add_argument("--audit-url")
    d.add_argument("--record", action="store_true", help="append the decision to the ledger")
    d.add_argument("--dotenv", type=Path)
    d.set_defaults(fn=cmd_decide)

    o = sub.add_parser("outcome")
    o.add_argument("--ledger", type=Path, required=True)
    o.add_argument("--decision-id", required=True)
    o.add_argument("--result", required=True, choices=sorted(ledger.BAD | ledger.GOOD))
    o.add_argument("--detail", default="")
    o.add_argument("--seconds", type=float)
    o.set_defaults(fn=cmd_outcome)

    s = sub.add_parser("state")
    s.add_argument("--ledger", type=Path, required=True)
    s.set_defaults(fn=cmd_state)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
