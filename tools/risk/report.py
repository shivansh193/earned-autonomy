"""Trust ledger report: one self-contained HTML page (no external assets).

Everything on the page is recomputed from the ledger with ``ledger.state`` at each
outcome, so it shows exactly the calibration that later decisions used.

    ea-ledger report --ledger .trust/ledger.jsonl --out public/index.html
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from risk import ledger

# CI energy, Cloud Carbon Footprint's method: a GitLab.com small Linux runner is 2 vCPU and
# 8 GiB; 2.485 W per vCPU at 50% utilisation, 0.392 W per GiB, PUE 1.1. Grid intensity of
# the runners' region (GCP us-east1). VERIFY both against the published sources before
# quoting; they are estimates, shown as such on the page.
RUNNER = {"vcpu": 2, "gib": 8, "w_per_vcpu": 2.485, "w_per_gib": 0.392, "pue": 1.1, "kg_co2e_per_kwh": 0.56}


def runner_g_co2e(seconds: float) -> float:
    watts = (RUNNER["vcpu"] * RUNNER["w_per_vcpu"] + RUNNER["gib"] * RUNNER["w_per_gib"]) * RUNNER["pue"]
    return watts * seconds / 3600 / 1000 * RUNNER["kg_co2e_per_kwh"] * 1000


def pipeline_seconds(api: str, project: str, pipeline_id: Any) -> float | None:
    """Sum of job durations of a pipeline and its child pipelines (public project API)."""
    import urllib.parse
    import urllib.request

    if not pipeline_id:
        return None
    base = f"{api}/projects/{urllib.parse.quote(project, safe='')}/pipelines/{pipeline_id}"
    try:
        with urllib.request.urlopen(f"{base}/jobs?per_page=100", timeout=30) as r:
            total = sum(j.get("duration") or 0 for j in json.load(r))
        with urllib.request.urlopen(f"{base}/bridges?per_page=100", timeout=30) as r:
            for b in json.load(r):
                child = (b.get("downstream_pipeline") or {}).get("id")
                if child:
                    total += pipeline_seconds(api, project, child) or 0
        return total
    except Exception:
        return None


def footprint(entries: list[dict[str, Any]], api: str, project: str) -> dict[str, dict[str, float]]:
    """CI seconds and g CO2e per release: the MR's evidence pipeline plus the production
    promotion pipeline (prod deploy and monitor). Keyed by decision id."""
    out = {}
    for d in (e for e in entries if e.get("type") == "decision"):
        secs = [s for s in (pipeline_seconds(api, project, d.get("pipeline_id")),
                            pipeline_seconds(api, project, d.get("deployed_pipeline_id"))) if s]
        if secs:
            total = sum(secs)
            out[d["id"]] = {"ci_minutes": round(total / 60, 1), "g_co2e": round(runner_g_co2e(total), 1)}
    return out

SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]  # categorical slots 1-3 (validated all-pairs); more areas fold
SERIES_DARK = ["#3987e5", "#d95926", "#199e70"]
LEVEL = {"hands_off": ("Hands-off", "lv-ho"), "supervised": ("Supervised", "lv-su"),
         "assisted": ("Assisted", "lv-as"), "blocked": ("Blocked", "lv-bl")}


def releases(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decision + outcome pairs in outcome order, each with the calibration state right after it."""
    decisions = {e["id"]: e for e in entries if e.get("type") == "decision"}
    out = []
    for i, e in enumerate(entries):
        if e.get("type") != "outcome" or e.get("decision_id") not in decisions:
            continue
        st = ledger.state(entries[: i + 1])
        out.append({"decision": decisions[e["decision_id"]], "outcome": e,
                    "penalties": st["area_penalties"], "thresholds": st["thresholds"]})
    return out


def _esc(x: Any) -> str:
    return html.escape(str(x), quote=True)


def penalty_chart(rel: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Step chart of each area's penalty after each release. Returns (svg, areas shown)."""
    areas: list[str] = []
    for r in rel:
        for a in r["penalties"]:
            if a not in areas:
                areas.append(a)
    if not areas:
        return "", []
    areas = areas[: len(SERIES)]
    w, h, l, rt, t, b = 880, 300, 56, 24, 20, 56
    n = len(rel) + 1  # x = 0 is "before the first release"
    ymax = max(ledger.AREA_PENALTY_CAP, 0.3)
    x = lambda i: l + (w - l - rt) * i / max(1, n - 1)
    y = lambda v: t + (h - t - b) * (1 - v / ymax)
    grid = "".join(
        f'<line x1="{l}" x2="{w - rt}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
        f'<text x="{l - 10}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">{v:.2f}</text>'
        for v in (0, 0.15, 0.3, 0.45, 0.6))
    labels = '<text x="{:.1f}" y="{}" class="tick" text-anchor="middle">start</text>'.format(x(0), h - b + 22)
    for i, r in enumerate(rel, 1):
        labels += f'<text x="{x(i):.1f}" y="{h - b + 22}" class="tick" text-anchor="middle">!{_esc(r["decision"].get("mr_iid"))}</text>'
        if r["outcome"]["result"] in ledger.BAD:
            labels += (f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{t}" y2="{h - b}" class="bad-rule"/>'
                       f'<text x="{x(i) + 6:.1f}" y="{t + 12}" class="note">{_esc(r["outcome"]["result"])}</text>')
    lines, dots = "", ""
    for k, a in enumerate(areas):
        vals = [0.0] + [r["penalties"].get(a, 0.0) for r in rel]
        d = f"M{x(0):.1f},{y(vals[0]):.1f}"
        for i in range(1, n):
            d += f" H{x(i):.1f} V{y(vals[i]):.1f}"
        lines += f'<path d="{d}" class="s{k}" fill="none" stroke-width="2"/>'
        for i in range(1, n):
            r = rel[i - 1]
            tip = (f'{a}: {vals[i]:.2f} after MR !{r["decision"].get("mr_iid")} '
                   f'({r["outcome"]["result"]}, {LEVEL.get(r["decision"]["level"], (r["decision"]["level"],))[0]})')
            dots += (f'<circle cx="{x(i):.1f}" cy="{y(vals[i]):.1f}" r="5" class="dot s{k}"/>'
                     f'<circle cx="{x(i):.1f}" cy="{y(vals[i]):.1f}" r="14" class="hit" data-tip="{_esc(tip)}"/>')
        last = vals[-1]
        lines += (f'<text x="{x(n - 1) - 8:.1f}" y="{y(last) - 10:.1f}" class="direct" text-anchor="end">'
                  f'{_esc(a)}  {last:.2f}</text>')
    svg = (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Area penalty after each release">'
           f'{grid}<line x1="{l}" x2="{w - rt}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="axis"/>{labels}{lines}{dots}'
           f'<text x="{l}" y="{h - 8}" class="tick">release (MR), in the order outcomes were recorded</text></svg>')
    return svg, areas


def render(entries: list[dict[str, Any]], *, source: str = "", generated: str = "",
           notes: dict[str, str] | None = None, footprints: dict[str, dict[str, float]] | None = None) -> str:
    notes = notes or {}
    footprints = footprints or {}
    rel = releases(entries)
    st = ledger.state(entries)
    th = st["thresholds"]
    bad = [r for r in rel if r["outcome"]["result"] in ledger.BAD]
    recov = [r["outcome"]["seconds_to_recover"] for r in bad if r["outcome"].get("seconds_to_recover") is not None]
    chart, areas = penalty_chart(rel)
    legend = "".join(f'<span class="key"><i class="sw s{k}"></i>{_esc(a)}</span>' for k, a in enumerate(areas))

    tiles = [
        ("Hands-off threshold", f"≤ {th['hands_off_max']:.2f}", f"starts at {ledger.INITIAL['hands_off_max']:.2f}"),
        ("Supervised threshold", f"≤ {th['supervised_max']:.2f}", f"starts at {ledger.INITIAL['supervised_max']:.2f}"),
        ("Clean Hands-off streak", f"{st['clean_hands_off_streak']} / {ledger.PROMOTE_AFTER}", "to widen Hands-off one step"),
        ("Releases recorded", str(len(rel)), f"{len(bad)} rolled back"),
        ("Time to recover", f"{sum(recov) / len(recov):.1f} s" if recov else "–", "mean, automatic rollbacks"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="tl">{_esc(a)}</div><div class="tv">{_esc(b)}</div>'
                         f'<div class="ts">{_esc(c)}</div></div>' for a, b, c in tiles)

    pen_rows = "".join(f"<tr><td><code>{_esc(a)}</code></td><td class='num'>+{v:.2f}</td></tr>"
                       for a, v in sorted(st["area_penalties"].items(), key=lambda kv: -kv[1]))
    pen_table = (f"<table><thead><tr><th>Area</th><th class='num'>Penalty now</th></tr></thead><tbody>{pen_rows}</tbody></table>"
                 if pen_rows else "<p class='muted'>No area carries a penalty right now.</p>")

    rows = ""
    for r in reversed(rel):
        d, o = r["decision"], r["outcome"]
        name, cls = LEVEL.get(d["level"], (d["level"], ""))
        rule = d.get("rule_level")
        rule_txt = f" <span class='muted'>(rules: {_esc(LEVEL.get(rule, (rule,))[0])})</span>" if rule and rule != d["level"] else ""
        res = o["result"]
        res_html = (f"<span class='res bad'>✕ {_esc(res)}</span>" if res in ledger.BAD else f"<span class='res ok'>✓ {_esc(res)}</span>")
        if o.get("seconds_to_recover") is not None:
            res_html += f" <span class='muted'>in {o['seconds_to_recover']} s</span>"
        note = notes.get(str(d.get("mr_iid")))
        risk = "" if d.get("final") is None else f"{d['final']:.2f}"
        rows += (f"<tr><td>{_esc(o['ts'][:16].replace('T', ' '))}</td><td>!{_esc(d.get('mr_iid'))}</td>"
                 f"<td><span class='lv {cls}'>{_esc(name)}</span>{rule_txt}</td>"
                 f"<td class='num'>{risk}</td><td>{res_html}</td>"
                 f"<td class='areas'>{', '.join(f'<code>{_esc(a)}</code>' for a in d.get('areas', []))}"
                 f"{f'<div class=note-row>{_esc(note)}</div>' if note else ''}</td></tr>")

    hist = "".join(f"<li>{_esc(h['ts'] or 'start')}: {_esc(h['event'])} → Hands-off ≤ {h['hands_off_max']:.2f}, "
                   f"Supervised ≤ {h['supervised_max']:.2f}</li>" for h in st["threshold_history"])

    dark_series = "".join(f".s{k}{{stroke:{c};fill:{c}}} .sw.s{k}{{background:{c}}}" for k, c in enumerate(SERIES_DARK))
    light_series = "".join(f".s{k}{{stroke:{c};fill:{c}}} .sw.s{k}{{background:{c}}}" for k, c in enumerate(SERIES))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trust Ledger</title>
<style>
:root{{color-scheme:light;--bg:#fcfcfb;--panel:#f3f2ef;--ink:#0b0b0b;--ink2:#52514e;--muted:#6b6a66;--line:#e2e0db;--bad:#c62828;--ok:#1b7f3b;
--ho:#1b7f3b;--su:#8a6100;--as:#b4521a;--bl:#c62828}}
@media (prefers-color-scheme: dark){{:root{{color-scheme:dark;--bg:#1a1a19;--panel:#242422;--ink:#f0efec;--ink2:#c3c2b7;--muted:#a3a29a;--line:#34342f;
--bad:#ff7b72;--ok:#56d364;--ho:#56d364;--su:#e3b341;--as:#f0883e;--bl:#ff7b72}} {dark_series}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,"Segoe UI",sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:32px 16px 64px}}
h1{{font-size:30px;margin:0 0 4px;letter-spacing:-.02em}} h2{{font-size:19px;margin:40px 0 12px}}
.lead{{color:var(--ink2);margin:0 0 24px;max-width:760px}} .muted{{color:var(--muted)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
.tile{{background:var(--panel);border-radius:10px;padding:14px 16px}} .tl{{font-size:13px;color:var(--ink2)}}
.tv{{font-size:28px;font-weight:650;margin:2px 0;font-variant-numeric:tabular-nums}} .ts{{font-size:12px;color:var(--muted)}}
.chart{{background:var(--panel);border-radius:10px;padding:12px 8px;position:relative}} svg{{width:100%;height:auto;display:block}}
.grid{{stroke:var(--line);stroke-width:1}} .axis{{stroke:var(--muted);stroke-width:1}} .tick{{fill:var(--muted);font-size:12px}}
.note{{fill:var(--bad);font-size:12px}} .bad-rule{{stroke:var(--bad);stroke-dasharray:3 4;stroke-width:1}}
.direct{{fill:var(--ink);font-size:13px;font-weight:600}} .dot{{stroke:var(--panel);stroke-width:2}} .hit{{fill:transparent;cursor:default}}
{light_series} path.s0,path.s1,path.s2{{fill:none}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;margin:8px 4px 0;font-size:13px;color:var(--ink2)}} .sw{{display:inline-block;width:12px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}}
#tip{{position:absolute;pointer-events:none;background:var(--ink);color:var(--bg);font-size:13px;padding:6px 9px;border-radius:6px;display:none;max-width:320px}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{font-size:12px;color:var(--ink2);font-weight:600}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
code{{font-size:12.5px;background:var(--panel);padding:1px 5px;border-radius:4px}} .areas code{{display:inline-block;margin:1px 0}}
.lv{{font-weight:650}} .lv-ho{{color:var(--ho)}} .lv-su{{color:var(--su)}} .lv-as{{color:var(--as)}} .lv-bl{{color:var(--bl)}}
.res.bad{{color:var(--bad);font-weight:600}} .res.ok{{color:var(--ok)}} .note-row{{font-size:12px;color:var(--muted);margin-top:4px}}
footer{{margin-top:40px;font-size:13px;color:var(--muted)}} ul{{padding-left:20px}}
</style></head><body><main>
<h1>Trust ledger</h1>
<p class="lead">Every production release of this project and what happened next. Earned Autonomy recomputes these
thresholds and penalties from the ledger before every merge-request decision: a failure in production makes the
next change to the same area need more human oversight, and clean releases earn it back.</p>
<div class="tiles">{tiles_html}</div>

<h2>Penalty per area, after each release</h2>
<div class="chart" id="chart">{chart or "<p class='muted' style='padding:8px'>No area has been penalised yet.</p>"}<div id="tip"></div></div>
<div class="legend">{legend if len(areas) > 1 else ''}</div>
<p class="muted" style="font-size:13px">A bad outcome adds +{ledger.AREA_PENALTY:.2f} to every area the change touched (cap {ledger.AREA_PENALTY_CAP:.2f});
each clean release that touches the area halves it. The penalty is added to the risk score of any MR touching that area.</p>
<h2>Penalties now</h2>{pen_table}

<h2>Releases</h2>
<div class="scroll"><table><thead><tr><th>Recorded (UTC)</th><th>MR</th><th>Level shipped</th><th class="num">Risk</th><th>Outcome</th><th>Areas</th></tr></thead>
<tbody>{rows}</tbody></table></div>

<h2>Threshold changes</h2><ul>{hist}</ul>
{_footprint_html(rel, footprints)}
<footer>Generated {_esc(generated)} from <code>{_esc(source)}</code> ({len(entries)} entries). The ledger is append-only;
this page is a pure function of it (<code>ea-ledger report</code>).</footer>
</main>
<script>
(function(){{var c=document.getElementById('chart'),t=document.getElementById('tip');if(!c)return;
c.addEventListener('mousemove',function(e){{var h=e.target.closest&&e.target.closest('.hit');if(!h){{t.style.display='none';return}}
var r=c.getBoundingClientRect();t.textContent=h.getAttribute('data-tip');t.style.display='block';
t.style.left=Math.min(e.clientX-r.left+12,r.width-330)+'px';t.style.top=(e.clientY-r.top+12)+'px'}});
c.addEventListener('mouseleave',function(){{t.style.display='none'}})}})();
</script></body></html>
"""


def _footprint_html(rel: list[dict[str, Any]], fp: dict[str, dict[str, float]]) -> str:
    rows = [(r, fp[r["decision"]["id"]]) for r in rel if r["decision"]["id"] in fp]
    if not rows:
        return ""
    total_min = sum(f["ci_minutes"] for _, f in rows)
    total_g = sum(f["g_co2e"] for _, f in rows)
    body = "".join(f"<tr><td>!{_esc(r['decision'].get('mr_iid'))}</td><td class='num'>{f['ci_minutes']:.1f}</td>"
                   f"<td class='num'>{f['g_co2e']:.1f}</td></tr>" for r, f in reversed(rows))
    return (f"<h2>Footprint</h2><p class='muted' style='font-size:13px'>CI time per release (the MR's evidence "
            f"pipeline plus the production promotion), turned into energy and emissions with Cloud Carbon Footprint's "
            f"method; estimates. Production itself scales to zero: no always-on instances.</p>"
            f"<div class='scroll'><table><thead><tr><th>MR</th><th class='num'>CI minutes</th>"
            f"<th class='num'>g CO2e</th></tr></thead><tbody>{body}<tr><td><b>Total</b></td>"
            f"<td class='num'><b>{total_min:.1f}</b></td><td class='num'><b>{total_g:.1f}</b></td></tr>"
            f"</tbody></table></div>")


def main_report(ledger_path: Path, out: Path, source: str, generated: str, notes_path: Path | None,
                api: str | None = None, project: str | None = None) -> None:
    entries = ledger.load(ledger_path)
    notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path and notes_path.exists() else {}
    fp = footprint(entries, api, project) if api and project else {}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(entries, source=source, generated=generated, notes=notes, footprints=fp), encoding="utf-8")
