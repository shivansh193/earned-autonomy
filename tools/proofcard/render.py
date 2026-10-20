"""Proof card: one MR note per pipeline that explains the autonomy decision.

Readers: the human approver (Supervised/Assisted), the reviewer after the fact
(Hands-off), and the Duo agents, which read the machine block at the bottom.
"""

from __future__ import annotations

import json
from typing import Any

MARKER = "<!-- earned-autonomy:proofcard v1 -->"

LEVEL_TEXT = {
    "hands_off": ("Hands-off", "Ships to production with no human step."),
    "supervised": ("Supervised", "One approval on this MR promotes it, with this card as the evidence."),
    "assisted": ("Assisted", "Each step (merge, staging, production) needs its own approval."),
    "blocked": ("Blocked", "Not promoted at any level until the blocking reasons are fixed."),
}


def _fmt(x: Any) -> str:
    return "n/a" if x is None else str(x)


def _security(findings: list[dict[str, Any]] | None) -> list[str]:
    if findings is None:
        return ["_No dependency scan results._"]
    if not findings:
        return ["No known vulnerabilities in dependencies."]
    out = ["| Finding | New in this MR | Severity | Static reachability | Runtime hits | Verdict |", "|---|---|---|---|---|---|"]
    for f in findings:
        reach = f.get("static_reachable")
        hits = f.get("runtime_hits")
        if reach is True and (hits is None or hits > 0):
            verdict = "fix now"
        elif reach is True or reach in ("possible", "unknown", None):
            verdict = "fix soon (reachable, not exercised)" if reach is True else "review"
        else:
            verdict = "defer, with evidence"
        reach_txt = {True: "reachable", False: "not reachable", "possible": "possibly", "unknown": "unknown"}.get(reach, "unknown (no function mapped)")
        new = {True: "yes", False: "no (pre-existing, not scored)", None: "unknown (scored)"}[f.get("new")]
        out.append(f"| `{f.get('cve')}` {f.get('package')} `{f.get('function')}` | {new} | {f.get('severity')} "
                   f"| {reach_txt} | {_fmt(hits)} | {verdict} |")
    for f in findings:
        if f.get("paths"):
            out.append(f"\n<details><summary>Call path for {f.get('cve')}</summary>\n\n"
                       + "\n".join(f"- {' → '.join(f'`{p}`' for p in path)}" for path in f["paths"])
                       + "\n\n</details>")
    return out


def _replay(r: dict[str, Any] | None) -> list[str]:
    if r is None:
        return ["_No replay evidence._"]
    lines = [
        f"Replayed **{_fmt(r.get('requests'))}** recorded requests over {_fmt(r.get('endpoints'))} endpoints "
        f"(compared against the {r.get('reference') or 'recording'}).",
        f"- Status mismatches: **{r.get('status_mismatches', 0)}**",
        f"- Body diffs: **{r.get('body_diffs_real', 0)} real**, {r.get('body_diffs_noise', 0)} noise",
        f"- p95 latency: {_fmt(r.get('p95_ms_before'))} → {_fmt(r.get('p95_ms_after'))} ms"
        + ("" if r.get("latency_comparable") else " (vs. the recording's environment: informational, not scored)"),
    ]
    for d in [d for d in r.get("body_diffs", []) if d.get("verdict") != "noise"][:5]:
        first = d["diffs"][0] if d.get("diffs") else {}
        lines.append(f"  - `{d['endpoint']}` {first.get('path', '')}: expected {_fmt(first.get('expected'))}, "
                     f"got {_fmt(first.get('actual'))} ({d.get('verdict')}{': ' + d['reason'] if d.get('reason') else ''})")
    return lines


def _infra(i: dict[str, Any] | None, touched: bool) -> list[str]:
    if not i:
        return ["Infra files changed but no plan evidence." if touched else "No infrastructure changes."]
    lines = [f"- `{c.get('type')}` {c.get('resource')}" + (f" — **risky**: {c.get('reason')}" if c.get("risky") else "")
             for c in i.get("changes", [])]
    lines.append(f"- Estimated cost delta: ${i.get('cost_delta_usd_month', 0):.2f}/month")
    return lines


def render(bundle: dict[str, Any], *, audit_url: str | None = None) -> str:
    risk, auto = bundle["risk"], bundle["autonomy"]
    name, meaning = LEVEL_TEXT[auto["level"]]
    th = auto["thresholds"]
    cs = bundle.get("change_summary") or {}
    out = [
        MARKER,
        f"## Autonomy: {name}",
        "",
        f"**Risk {risk['final']:.2f}**. Thresholds: hands-off ≤ {th['hands_off_max']:.2f}, "
        f"supervised ≤ {th['supervised_max']:.2f}. {meaning}",
    ]
    if auto.get("blocked_reasons"):
        out.append("\n**Blocked:** " + "; ".join(auto["blocked_reasons"]))

    out += ["", "### Why this level", "", "| Evidence | Points |", "|---|---|"]
    for c in risk["contributions"]:
        out.append(f"| {c['detail']} | +{c['points']:.2f} |")
    for t in risk.get("trust_items", []):
        out.append(f"| Trust ledger: past miss in `{t['area']}` | +{t['penalty']:.2f} |")
    if risk.get("agent_adjustment"):
        out.append(f"| Autonomy Router agent: {risk.get('agent_note') or 'raised risk'} | +{risk['agent_adjustment']:.2f} |")
    elif risk.get("agent_note"):
        out.append(f"| Autonomy Router agent: {risk['agent_note']} | +0.00 |")
    if not (risk["contributions"] or risk.get("trust_items") or risk.get("agent_adjustment")):
        out.append("| No risk signals | 0.00 |")
    out.append(f"| **Total** | **{risk['final']:.2f}** |")

    tests = bundle.get("tests")
    out += ["", "### Tests", "",
            "_No test results._" if tests is None else
            f"{tests.get('passed', 0)} passed, {tests.get('failed', 0)} failed, {tests.get('skipped', 0)} skipped"]
    out += ["", "### Security (static reachability + runtime hits)", "", *_security(bundle.get("security"))]
    out += ["", "### Behaviour (replay of recorded traffic)", "", *_replay(bundle.get("replay"))]
    out += ["", "### Infrastructure", "", *_infra(bundle.get("infra"), cs.get("infra_touched", False))]
    out += ["", "### Scope", "",
            f"{cs.get('files', 0)} files; routes touched: "
            + (", ".join(f"`{r}`" for r in cs.get("routes_touched", [])) or "none")]
    prov = bundle.get("provenance") or {}
    footer = f"Pipeline {_fmt(prov.get('pipeline_id'))} · commit `{_fmt(bundle.get('commit'))}` · {prov.get('timestamp', '')}"
    if audit_url:
        footer += f" · [audit record]({audit_url})"
    out += ["", "---", footer, "",
            "<details><summary>Machine-readable decision</summary>", "", "```json",
            json.dumps({"level": auto["level"], "risk": risk["final"], "thresholds": th,
                        "areas": bundle.get("areas", []), "blocked": auto.get("blocked_reasons", [])}, indent=2),
            "```", "", "</details>"]
    return "\n".join(out) + "\n"
