import json
from pathlib import Path

from callgraph import build_index
from proofcard import render
from risk import ledger
from risk.bundle import build_bundle, replay_summary, routes_touched, security_findings, junit_summary
from risk.cli import main as risk_main
from risk.engine import decide

FIX = Path(__file__).parent / "fixtures" / "cg"
ITEMS = "GET /api/v1/items/"
TH = dict(ledger.INITIAL)


def clean_replay():
    return replay_summary({"reference": "recording", "requests": 166, "endpoints": 19,
                           "status_mismatches": 0, "body_diff_details": [],
                           "p95_ms_before": 80, "p95_ms_after": 78}, None)


def bundle(routes=(ITEMS,), security=(), replay="clean", tests=None, infra=None, changed=("backend/app/x.py",)):
    return build_bundle(
        mr_iid=7, commit="abc", pipeline_id=1, changed_files=list(changed), routes=list(routes),
        tests=tests if tests is not None else {"passed": 60, "failed": 0, "skipped": 0},
        security=list(security), replay=clean_replay() if replay == "clean" else replay, infra=infra)


# ------------------------------------------------------------------- engine
def test_clean_change_is_hands_off():
    d = decide(bundle(), TH, {})
    assert d["risk"]["final"] == 0.0 and d["autonomy"]["level"] == "hands_off"


def test_failing_or_missing_tests_block_at_every_level():
    assert decide(bundle(tests={"passed": 1, "failed": 2, "skipped": 0}), TH, {})["autonomy"]["level"] == "blocked"
    b = bundle()
    b["tests"] = None
    assert decide(b, TH, {})["autonomy"]["blocked_reasons"] == ["no test results"]


def test_reachable_but_not_exercised_cve_is_supervised_scale():
    f = {"cve": "CVE-1", "package": "p", "function": "p.f", "severity": "high",
         "static_reachable": True, "paths": [], "runtime_hits": 0}
    d = decide(bundle(security=[f]), TH, {})
    assert d["risk"]["final"] == 0.15 and d["autonomy"]["level"] == "hands_off"
    f2 = dict(f, runtime_hits=4)
    d2 = decide(bundle(security=[f2]), TH, {})
    assert d2["risk"]["final"] == 0.5 and d2["autonomy"]["level"] == "supervised"
    f3 = dict(f, runtime_hits=None)  # no runtime evidence: assume exercised
    assert decide(bundle(security=[f3]), TH, {})["risk"]["final"] == 0.5
    f4 = dict(f, static_reachable=False)
    assert decide(bundle(security=[f4]), TH, {})["risk"]["final"] == 0.0


def test_two_reachable_findings_reach_supervised():
    fs = [{"cve": f"CVE-{i}", "package": "p", "function": f"p.f{i}", "severity": "medium",
           "static_reachable": True, "paths": [], "runtime_hits": 0} for i in range(2)]
    d = decide(bundle(security=fs), TH, {})
    assert d["risk"]["final"] == 0.3 and d["autonomy"]["level"] == "supervised"


def test_replay_regression_and_unclassified_diffs_count_as_real():
    rep = replay_summary({"requests": 10, "endpoints": 2, "status_mismatches": 0,
                          "body_diff_details": [{"seq": 4, "endpoint": ITEMS, "diffs": [{"path": "$.data", "kind": "length", "expected": 6, "actual": 2}]}]}, None)
    assert rep["body_diffs_real"] == 1
    d = decide(bundle(replay=rep), TH, {})
    assert d["autonomy"]["level"] == "assisted" and d["risk"]["final"] == 0.6
    rep_noise = replay_summary({"requests": 10, "endpoints": 2, "status_mismatches": 0,
                                "body_diff_details": [{"seq": 4, "endpoint": ITEMS, "diffs": []}]},
                               [{"seq": 4, "verdict": "noise", "reason": "data drift"}])
    assert decide(bundle(replay=rep_noise), TH, {})["autonomy"]["level"] == "hands_off"


def test_missing_replay_and_unplanned_infra_are_conservative():
    assert decide(bundle(replay=None), TH, {})["risk"]["final"] == 0.3
    d = decide(bundle(changed=("infra/main.tf",), routes=()), TH, {})
    assert d["autonomy"]["level"] == "assisted"


def test_risky_infra_escalates_to_assisted():
    infra = {"changes": [{"type": "replace", "resource": "google_sql_database_instance.db", "risky": True,
                          "reason": "replaces a stateful resource"}], "cost_delta_usd_month": 0.0}
    d = decide(bundle(infra=infra, routes=()), TH, {})
    assert d["autonomy"]["level"] == "assisted"


def test_agent_can_raise_but_never_lower():
    up = decide(bundle(), TH, {}, {"adjustment": 0.25, "reason": "touches auth"})
    assert up["risk"]["final"] == 0.25 and up["autonomy"]["level"] == "supervised"
    f = {"cve": "CVE-1", "package": "p", "function": "p.f", "severity": "high",
         "static_reachable": True, "paths": [], "runtime_hits": 3}
    down = decide(bundle(security=[f]), TH, {}, {"adjustment": -0.4, "reason": "looks fine to me"})
    assert down["risk"]["final"] == 0.5
    assert "ignored" in down["risk"]["agent_note"]


# ------------------------------------------------------------------- ledger
def run_outcome(entries, b, result, th=None, pens=None):
    st = ledger.state(entries)
    d = decide(b, th or st["thresholds"], pens if pens is not None else st["area_penalties"])
    e = ledger.decision_entry(b, d)
    entries.append(e)
    entries.append(ledger.outcome_entry(e["id"], result))
    return d


def test_scenario_4_miss_tightens_threshold_and_routes_next_items_change_to_supervised():
    entries = []
    d1 = run_outcome(entries, bundle(), "rollback")  # tests passed, hands-off, prod replay caught it
    assert d1["autonomy"]["level"] == "hands_off"
    st = ledger.state(entries)
    assert st["thresholds"]["hands_off_max"] == 0.15
    assert st["area_penalties"] == {f"route:{ITEMS}": 0.3}
    assert len(st["threshold_history"]) == 2

    nxt = decide(bundle(), st["thresholds"], st["area_penalties"])  # next, equally clean, /items change
    assert nxt["autonomy"]["level"] == "supervised"
    assert nxt["risk"]["trust_items"] == [{"area": f"route:{ITEMS}", "penalty": 0.3}]
    other = decide(bundle(routes=("GET /api/v1/users/me",)), st["thresholds"], st["area_penalties"])
    assert other["autonomy"]["level"] == "hands_off"  # unrelated area keeps its autonomy


def test_clean_outcomes_decay_penalty_and_earn_back_threshold():
    entries = []
    run_outcome(entries, bundle(), "rollback")
    for _ in range(3):  # supervised (penalised) clean deploys halve the penalty 0.3 -> 0.15 -> 0.075 -> gone
        run_outcome(entries, bundle(), "clean")
    st = ledger.state(entries)
    assert st["area_penalties"] == {}
    assert st["thresholds"]["hands_off_max"] == 0.15
    for _ in range(ledger.PROMOTE_AFTER):
        run_outcome(entries, bundle(routes=("GET /x",)), "clean")
    assert ledger.state(entries)["thresholds"]["hands_off_max"] == 0.2


def test_thresholds_respect_floors_and_ordering():
    entries = []
    for _ in range(10):
        run_outcome(entries, bundle(routes=()), "rollback", th={"hands_off_max": 0.9, "supervised_max": 0.95}, pens={})
    st = ledger.state(entries)
    assert st["thresholds"]["hands_off_max"] == ledger.FLOORS["hands_off_max"]
    assert st["thresholds"]["hands_off_max"] < st["thresholds"]["supervised_max"]


def test_unknown_outcome_rejected():
    import pytest

    with pytest.raises(ValueError):
        ledger.outcome_entry("x", "meh")


# ------------------------------------------------------------- bundle inputs
def test_routes_touched_follows_call_graph():
    idx = build_index(FIX / "app", ["shop"])
    root = FIX / "app"
    assert routes_touched(idx, ["shop/helpers.py"], root) == ["GET /api/v1/items/"]
    assert routes_touched(idx, ["shop/security.py"], root) == ["GET /api/v1/items/"]  # via Depends
    assert routes_touched(idx, ["shop/yamlish_user.py"], root) == []


def test_security_findings_join_hits_by_function():
    reach = [{"cve": "C", "package": "p", "function": "fakejwt.decode", "resolved_function": "fakejwt.api_jwt.PyJWT.decode",
              "reachable": True, "paths": [["GET /a", "x"]], "severity": "High"}]
    out = security_findings(reach, {"hits": {"fakejwt.api_jwt.PyJWT.decode": 7}})
    assert out[0]["runtime_hits"] == 7 and out[0]["severity"] == "high"
    assert security_findings(reach, None)[0]["runtime_hits"] is None


def test_junit(tmp_path):
    p = tmp_path / "r.xml"
    p.write_text('<testsuites><testsuite tests="10" failures="1" errors="1" skipped="2"/></testsuites>')
    assert junit_summary(p) == {"passed": 6, "failed": 2, "skipped": 2}


# ------------------------------------------------------------------ outputs
def test_proofcard_explains_decision():
    st_entries = []
    run_outcome(st_entries, bundle(), "rollback")
    st = ledger.state(st_entries)
    f = {"cve": "CVE-2026-1", "package": "pyjwt", "function": "jwt.decode", "severity": "high",
         "static_reachable": True, "paths": [["GET /api/v1/items/", "app.deps.get_current_user", "jwt.api_jwt.PyJWT.decode"]],
         "runtime_hits": 0}
    b = bundle(security=[f])
    b.update(decide(b, st["thresholds"], st["area_penalties"]))
    md = render(b, audit_url="https://example/audit")
    assert "## Autonomy: Supervised" in md
    assert "Trust ledger: past miss in `route:GET /api/v1/items/`" in md
    assert "statically reachable, 0 runtime hits" in md
    assert "`GET /api/v1/items/` → `app.deps.get_current_user`" in md
    assert '"level": "supervised"' in md


def test_cli_decide_records_and_writes_card(tmp_path):
    led = tmp_path / ".trust" / "ledger.jsonl"
    bp = tmp_path / "bundle.json"
    bp.write_text(json.dumps(bundle()))
    card = tmp_path / "card.md"
    env = tmp_path / "decision.env"
    assert risk_main(["decide", "--bundle", str(bp), "--ledger", str(led), "--out", str(bp),
                      "--proofcard", str(card), "--record", "--dotenv", str(env)]) == 0
    out = json.loads(bp.read_text())
    assert out["autonomy"]["level"] == "hands_off"
    did = out["provenance"]["decision_id"]
    assert f"EA_DECISION_ID={did}" in env.read_text()
    assert "Autonomy: Hands-off" in card.read_text(encoding="utf-8")
    assert out["proofcard_markdown"] == card.read_text(encoding="utf-8")
    assert risk_main(["outcome", "--ledger", str(led), "--decision-id", did, "--result", "rollback", "--seconds", "41"]) == 0
    assert ledger.state(ledger.load(led))["thresholds"]["hands_off_max"] == 0.15


# ------------------------------------------------- function-level scope, new findings
from risk.bundle import parse_diff  # noqa: E402

ITEMS_DIFF = """diff --git a/shop/api/routes/items.py b/shop/api/routes/items.py
--- a/shop/api/routes/items.py
+++ b/shop/api/routes/items.py
@@ -11 +11 @@ router = APIRouter(prefix="/items", tags=["items"])
-def list_items(session: SessionDep, user: CurrentUser, limit: int = 100):
+def list_items(session: SessionDep, user: CurrentUser, limit: int = 2):
"""


def test_parse_diff_handles_counts_and_deletions():
    d = parse_diff(ITEMS_DIFF + "--- a/x.py\n+++ b/x.py\n@@ -5,3 +4,0 @@\n-a\n-b\n-c\n@@ -20 +18,4 @@\n+x\n")
    assert d["shop/api/routes/items.py"] == [(11, 11)]
    assert d["x.py"] == [(4, 4), (18, 21)]
    assert "dev/null" not in "".join(parse_diff("--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n"))


def test_function_level_scope_only_marks_changed_route():
    idx = build_index(FIX / "app", ["shop"])
    root = FIX / "app"
    diff = parse_diff(ITEMS_DIFF)
    assert routes_touched(idx, ["shop/api/routes/items.py"], root, diff) == ["GET /api/v1/items/"]
    # file-level fallback would also mark the import route in the same file
    assert routes_touched(idx, ["shop/api/routes/items.py"], root) == ["GET /api/v1/items/", "POST /api/v1/items/import"]
    # a hunk in module-level code (the router line) falls back to the whole file
    mod_level = parse_diff("+++ b/shop/api/routes/items.py\n@@ -6 +6 @@\n")
    assert len(routes_touched(idx, [], root, mod_level)) == 2


def test_preexisting_findings_listed_but_not_scored():
    reach = [{"cve": "OLD", "package": "p", "function": "p.f", "reachable": True, "paths": [], "severity": "high"},
             {"cve": "NEW", "package": "q", "function": "q.g", "reachable": True, "paths": [], "severity": "medium"}]
    sec = security_findings(reach, {"hits": {"p.f": 9, "q.g": 0}}, baseline=[{"cve": "OLD", "package": "p"}])
    assert [s["new"] for s in sec] == [False, True]
    d = decide(bundle(security=sec), TH, {})
    assert d["risk"]["final"] == 0.15  # only NEW (reachable, 0 hits)
    b = bundle(security=sec)
    b.update(d)
    assert "no (pre-existing, not scored)" in render(b)
    unknown = security_findings(reach, {"hits": {}}, baseline=None)
    assert unknown[0]["new"] is None
    assert decide(bundle(security=unknown), TH, {})["risk"]["final"] > 0.15  # no baseline: scored


def test_ledger_report_replays_penalties_per_release():
    from risk import ledger
    from risk.report import releases, render

    def dec(mr, level, areas):
        return {"type": "decision", "id": f"d{mr}", "ts": "2026-10-22T00:00:00+00:00", "mr_iid": mr,
                "areas": areas, "level": level, "final": 0.3, "rule_score": 0.3, "thresholds": {}}

    entries = [dec(8, "assisted", ["route:GET /x"]), ledger.outcome_entry("d8", "rollback", seconds=3.3),
               dec(9, "supervised", ["route:GET /x"]), ledger.outcome_entry("d9", "clean")]
    rel = releases(entries)
    assert [r["penalties"].get("route:GET /x") for r in rel] == [0.3, 0.15]
    page = render(entries, notes={"9": "a <note>"})
    assert "3.3 s" in page and "rollback" in page and "a &lt;note&gt;" in page
    assert "<script src" not in page  # self-contained
def test_module_level_hunks_scope_to_the_symbol_they_change(tmp_path):
    """One file holding every route (VaultScope's api/main.py): editing a request model or
    a dispatch table touches only the routes that use it; a middleware block touches all."""
    pkg = tmp_path / "one"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    src = (
        '"""Doc."""\n'                                    # 1
        "from fastapi import FastAPI\n"                     # 2
        "from pydantic import BaseModel\n"                  # 3
        "app = FastAPI()\n"                                 # 4
        "class ReportRequest(BaseModel):\n"                 # 5
        "    type: str\n"                                   # 6
        "BUILDERS = {'a': 1}\n"                             # 7
        "if True:\n"                                        # 8
        "    app.add_middleware(object)\n"                  # 9
        "@app.post('/report')\n"                            # 10
        "def report(req: ReportRequest):\n"                 # 11
        "    return BUILDERS[req.type]\n"                   # 12
        "@app.get('/jobs')\n"                               # 13
        "def jobs():\n"                                     # 14
        "    return []\n"                                   # 15
    )
    (pkg / "main.py").write_text(src)
    idx = build_index(tmp_path, ["one"])
    rel = "one/main.py"
    assert routes_touched(idx, [], tmp_path, diff={rel: [(6, 6)]}) == ["POST /report"]
    assert routes_touched(idx, [], tmp_path, diff={rel: [(7, 7)]}) == ["POST /report"]
    assert routes_touched(idx, [], tmp_path, diff={rel: [(1, 1), (3, 3)]}) == []
    assert routes_touched(idx, [], tmp_path, diff={rel: [(9, 9)]}) == ["GET /jobs", "POST /report"]
    # calls into the standard library (not indexed) must not break the walk
    (pkg / "main.py").write_text(src.replace("    return []\n", "    import os\n    return os.environ.get('X')\n"))
    idx = build_index(tmp_path, ["one"])
    assert routes_touched(idx, [], tmp_path, diff={rel: [(6, 6)]}) == ["POST /report"]
