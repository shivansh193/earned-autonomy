"""ea-monitor: after a production promotion, prove the new revision behaves like the
previous one on the same production data, or roll back automatically.

1. Give the newest revision ("candidate") and the one before it ("previous")
   private tagged URLs; all real traffic stays where it is.
2. Replay the canary account's traffic against both (baseline mode: candidate is
   compared with previous, not with an old recording).
3. On any status mismatch or body difference: pin 100% of traffic back to the
   previous revision and measure the time to recover.
4. Append the release decision (from the MR's evidence bundle) and its outcome to
   the trust ledger in GCS, which recalibrates future decisions.
5. Exit 1 after a rollback, so the pipeline fails and the Rollback agent opens an
   incident with this report.

Environment: GCP_PROJECT, GCP_REGION, EA_LEDGER_BUCKET, EA_MR, EA_LEVEL, CI_* and
either GITLAB_OIDC_TOKEN (CI) or GCP_ACCESS_TOKEN (local).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from monitor.cloudrun import CloudRun, latest_with_tags, pinned_to, short, tag_urls
from replay.model import load
from replay.runner import compare, replay
from risk import gcs, ledger
from risk.gcs import _req


def mr_bundle(mr: str | None) -> dict[str, Any] | None:
    """The evidence bundle from the MR's latest pipeline (autonomy-decision job)."""
    api, pid = os.environ.get("CI_API_V4_URL"), os.environ.get("CI_PROJECT_ID")
    if not (mr and mr.isdigit() and api and pid):
        return None
    hdr = {"JOB-TOKEN": os.environ["CI_JOB_TOKEN"]} if os.environ.get("CI_JOB_TOKEN") else {}
    st, raw, _ = _req(f"{api}/projects/{pid}/merge_requests/{mr}/pipelines", headers=hdr)
    if st != 200:
        return None
    for p in json.loads(raw):
        st, raw, _ = _req(f"{api}/projects/{pid}/pipelines/{p['id']}/jobs?per_page=50", headers=hdr)
        job = next((j for j in json.loads(raw) if j["name"] == "autonomy-decision" and j["status"] == "success"), None) if st == 200 else None
        if job:
            st, raw, _ = _req(f"{api}/projects/{pid}/jobs/{job['id']}/artifacts/reports/bundle.json", headers=hdr)
            if st == 200:
                return json.loads(raw)
    return None


def ui_check(base: str) -> dict[str, Any]:
    """Does this revision serve the frontend: the page, its root element and the JS bundle?"""
    import re
    import urllib.request

    out: dict[str, Any] = {"page": None, "root": False, "bundle": None}
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/", timeout=120) as r:
            out["page"] = r.status
            html = r.read().decode("utf-8", "replace")
        out["root"] = 'id="root"' in html
        src = re.search(r'<script[^>]+src="([^"]+\.js)"', html)
        if src:
            with urllib.request.urlopen(base.rstrip("/") + "/" + src.group(1).lstrip("/"), timeout=60) as r:
                out["bundle"] = r.status
    except Exception as e:  # a revision that can't serve the page counts as not serving it
        out["error"] = str(e)[:120]
    return out


def ui_regression(previous: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """The candidate lost something the previous revision served."""
    return any(previous.get(k) == v and candidate.get(k) != v for k, v in (("page", 200), ("root", True), ("bundle", 200)))


def rolled_back_revisions(bucket: str, token: str) -> set[str]:
    data, _ = gcs.download(bucket, "trust/ledger.jsonl", token)
    bad = set()
    for line in data.decode().splitlines():
        e = json.loads(line) if line.strip() else {}
        if e.get("type") == "outcome" and e.get("result") == "rollback":
            try:
                bad.add(json.loads(e.get("detail") or "{}").get("candidate"))
            except ValueError:
                pass
    bad.discard(None)
    return bad


def decision_from(bundle: dict[str, Any] | None, mr: str | None, level: str | None) -> dict[str, Any]:
    if bundle and bundle.get("risk") and bundle.get("autonomy"):
        entry = ledger.decision_entry(bundle, {"risk": bundle["risk"], "autonomy": bundle["autonomy"]})
    else:
        entry = ledger.decision_entry({"mr_iid": int(mr) if mr and mr.isdigit() else None, "areas": ["files:unknown"]},
                                      {"risk": {"final": None, "rule_score": None},
                                       "autonomy": {"level": (level or "unknown").replace("-", "_"), "thresholds": {}}})
    # Record the level that was actually enforced (the Router may have raised the rule
    # engine's level); calibration must tighten the threshold of the level that shipped.
    enforced = (level or "").replace("-", "_")
    if enforced in {"hands_off", "supervised", "assisted", "blocked"}:
        entry["rule_level"] = entry["level"]
        entry["level"] = enforced
    entry["commit"] = os.environ.get("CI_COMMIT_SHA", entry.get("commit"))
    entry["deployed_pipeline_id"] = os.environ.get("CI_PIPELINE_ID")
    return entry


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ea-monitor")
    p.add_argument("--service", default="api-prod")
    p.add_argument("--traffic", type=Path, default=Path("traffic/canary.jsonl"))
    p.add_argument("--out", type=Path, default=Path("reports/monitor.json"))
    p.add_argument("--no-ledger", action="store_true")
    a = p.parse_args(argv)

    token = gcs.access_token()
    run = CloudRun(os.environ["GCP_PROJECT"], os.environ["GCP_REGION"], token)
    bucket = os.environ.get("EA_LEDGER_BUCKET")
    bad = rolled_back_revisions(bucket, token) if bucket else set()
    # Compare against the last known-good revision: never against one we rolled back.
    newest, *older = run.revisions(a.service) or [None]
    revs = ([newest] if newest else []) + [r for r in older if short(r["name"]) not in bad]
    result: dict[str, Any] = {"service": a.service, "revisions": [short(r["name"]) for r in revs[:3]]}
    if len(revs) < 2:
        result.update(outcome="clean", note="first revision: nothing to compare against")
        rolled_back = False
    else:
        candidate, previous = revs[0]["name"], revs[1]["name"]
        result.update(candidate=short(candidate), previous=short(previous))
        svc = run.set_traffic(a.service, latest_with_tags(previous, candidate))
        urls = tag_urls(svc)
        result["tag_urls"] = urls
        canary_pw = run.secret("ea-canary-password")
        exchanges = load(a.traffic)
        secrets = {"CANARY_PASSWORD": canary_pw}
        base = replay(exchanges, urls["previous"], secrets=secrets, timeout=120)
        cand = replay(exchanges, urls["candidate"], secrets=secrets, timeout=120)
        report = compare(exchanges, cand, base)
        result["replay"] = {k: report[k] for k in ("requests", "endpoints", "status_mismatches", "body_diffs",
                                                   "errors", "status_mismatch_details", "body_diff_details")}
        ui = {"previous": ui_check(urls["previous"]), "candidate": ui_check(urls["candidate"])}
        result["ui"] = ui
        regression = bool(report["status_mismatches"] or report["body_diffs"] or report["errors"]
                          or ui_regression(ui["previous"], ui["candidate"]))
        rolled_back = False
        if regression:
            t0 = time.time()
            run.set_traffic(a.service, pinned_to(previous, candidate))
            result["seconds_to_recover"] = round(time.time() - t0, 1)
            rolled_back = True
        result["outcome"] = "rollback" if rolled_back else "clean"

    mr, level = os.environ.get("EA_MR"), os.environ.get("EA_LEVEL")
    decision = decision_from(mr_bundle(mr), mr, level)
    outcome = ledger.outcome_entry(decision["id"], result["outcome"],
                                   detail=json.dumps({k: result.get(k) for k in ("candidate", "previous")}),
                                   seconds=result.get("seconds_to_recover"))
    result["ledger"] = {"decision": decision, "outcome": outcome}
    if not a.no_ledger and os.environ.get("EA_LEDGER_BUCKET"):
        gcs.append(os.environ["EA_LEDGER_BUCKET"], "trust/ledger.jsonl", [decision, outcome], token)
        data, _ = gcs.download(os.environ["EA_LEDGER_BUCKET"], "trust/ledger.jsonl", token)
        entries = [json.loads(l) for l in data.decode().splitlines() if l.strip()]
        st = ledger.state(entries)
        result["calibration"] = {"thresholds": st["thresholds"], "area_penalties": st["area_penalties"]}

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    rep = result.get("replay", {})
    print(f"{a.service}: {result.get('previous')} -> {result.get('candidate')}: "
          f"{rep.get('status_mismatches', 0)} status mismatches, {rep.get('body_diffs', 0)} body diffs -> {result['outcome']}"
          + (f" (rolled back in {result['seconds_to_recover']} s)" if rolled_back else ""))
    for d in rep.get("body_diff_details", [])[:3]:
        print("  diff", d["endpoint"], d["query"], d["diffs"][:2])
    if "calibration" in result:
        print("  calibration:", json.dumps(result["calibration"]))
    return 1 if rolled_back else 0


if __name__ == "__main__":
    raise SystemExit(main())
