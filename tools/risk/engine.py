"""Deterministic risk score and autonomy level.

The score is a sum of itemised, documented contributions (``docs/risk-model.md``)
so every decision can be explained line by line on the proof card.

Safety properties:
* failing tests block promotion at every level;
* missing evidence counts against the change, never for it;
* the agent's adjustment can only *raise* the score (a negative adjustment is
  recorded and ignored);
* per-area penalties from the trust ledger are added on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HIGH = {"high", "critical"}


@dataclass
class Rules:
    reachable_hit_high: float = 0.5
    reachable_hit_other: float = 0.3
    reachable_no_hits: float = 0.15
    possibly_reachable: float = 0.15
    replay_regression: float = 0.6
    latency_regression: float = 0.2
    missing_replay: float = 0.3
    infra_risky: float = 0.6
    infra_update: float = 0.15
    infra_cost: float = 0.1
    cost_threshold_usd_month: float = 5.0
    per_route_beyond_two: float = 0.05
    planning_prior_cap: float = 0.3
    area_penalty_cap: float = 0.6


@dataclass
class Score:
    contributions: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    def add(self, rule: str, points: float, detail: str) -> None:
        if points:
            self.contributions.append({"rule": rule, "points": round(points, 4), "detail": detail})

    @property
    def total(self) -> float:
        return round(sum(c["points"] for c in self.contributions), 4)


def rule_score(bundle: dict[str, Any], rules: Rules | None = None) -> Score:
    r = rules or Rules()
    s = Score()

    docs = (bundle.get("change_summary") or {}).get("docs_only") is True
    tests = bundle.get("tests")
    if docs and tests is None and bundle.get("replay") is None:
        # Nothing that runs or ships changed, so tests and replay can't tell us anything:
        # CI didn't spend the energy on them.
        s.add("evidence.not_needed", 0.0, "documentation only: tests, replay and browser tests not run")
    elif tests is None:
        s.blocked.append("no test results")
    elif tests.get("failed", 0) > 0:
        s.blocked.append(f"{tests['failed']} failing test(s)")

    for f in bundle.get("security") or []:
        reach, hits, sev = f.get("static_reachable"), f.get("runtime_hits"), f.get("severity", "unknown")
        name = f"{f.get('cve') or 'finding'} ({f.get('package')}, {f.get('function')})"
        if f.get("new") is False:
            # Already on the target branch: this change didn't introduce it. It is
            # listed on the card and handled by the Triage flow's fix MR instead.
            continue
        exercised = hits is None or hits > 0  # no runtime data: assume exercised
        if reach is True or reach in ("unknown", None):  # None: no vulnerable function mapped
            if exercised:
                pts = r.reachable_hit_high if sev in HIGH else r.reachable_hit_other
                why = "no runtime data" if hits is None else f"{hits} runtime hits"
                s.add("security.reachable_exercised", pts, f"{name}: reachable, {why}, severity {sev}")
            else:
                s.add("security.reachable_not_exercised", r.reachable_no_hits,
                      f"{name}: statically reachable, 0 runtime hits in replay")
        elif reach == "possible":
            s.add("security.possibly_reachable", r.possibly_reachable,
                  f"{name}: only via a call on an untyped receiver")

    replay = bundle.get("replay")
    if replay is None and docs:
        pass
    elif replay is None:
        s.add("replay.missing", r.missing_replay, "no replay evidence for this change")
    else:
        regressions = replay.get("status_mismatches", 0) + replay.get("body_diffs_real", 0)
        if regressions:
            s.add("replay.regression", r.replay_regression,
                  f"{replay.get('status_mismatches', 0)} status mismatches, "
                  f"{replay.get('body_diffs_real', 0)} real body diffs")
        widened = replay.get("access_widened") or []
        if widened:
            eps = sorted({w.get("endpoint", "?") for w in widened})
            s.blocked.append(f"{len(widened)} replayed request(s) that were refused now succeed "
                             f"(access widened: {', '.join(eps[:3])})")
        if replay.get("latency_regression"):
            s.add("replay.latency", r.latency_regression,
                  f"p95 {replay.get('p95_ms_before')} -> {replay.get('p95_ms_after')} ms (>20%)")

    infra = bundle.get("infra")
    if infra:
        risky = [c for c in infra.get("changes", []) if c.get("risky")]
        other = [c for c in infra.get("changes", []) if not c.get("risky") and c.get("type") != "no-op"]
        if risky:
            s.add("infra.risky", r.infra_risky,
                  "; ".join(f"{c['type']} {c['resource']}: {c.get('reason', 'risky')}" for c in risky))
        elif other:
            s.add("infra.update", r.infra_update, f"{len(other)} infra change(s)")
        cost = infra.get("cost_delta_usd_month") or 0.0
        if cost > r.cost_threshold_usd_month:
            s.add("infra.cost", r.infra_cost, f"cost delta ${cost:.2f}/month")
    elif (bundle.get("change_summary") or {}).get("infra_touched"):
        s.add("infra.missing_plan", r.infra_risky, "infra files changed but no terraform plan evidence")

    routes = (bundle.get("change_summary") or {}).get("routes_touched", [])
    if len(routes) > 2:
        s.add("change.size", r.per_route_beyond_two * (len(routes) - 2), f"{len(routes)} routes touched")

    prior = bundle.get("planning_prior")
    if prior:
        s.add("plan.prior", min(max(prior, 0.0), r.planning_prior_cap), "planning agent's risk prior")
    return s


def decide(
    bundle: dict[str, Any],
    thresholds: dict[str, float],
    area_penalties: dict[str, float],
    agent: dict[str, Any] | None = None,
    rules: Rules | None = None,
) -> dict[str, Any]:
    r = rules or Rules()
    s = rule_score(bundle, r)
    rule_total = s.total

    trust = 0.0
    trust_items = []
    for area in bundle.get("areas", []):
        p = area_penalties.get(area, 0.0)
        if p:
            trust_items.append({"area": area, "penalty": p})
            trust += p
    trust = round(min(trust, r.area_penalty_cap), 4)

    agent_adj, agent_note = 0.0, None
    if agent is not None:
        requested = float(agent.get("adjustment", 0.0))
        if requested < 0:
            agent_note = f"agent asked for {requested:+.2f}; ignored (agents may only raise risk)"
        else:
            agent_adj = round(min(requested, 1.0), 4)
        agent_note = agent_note or agent.get("reason")

    final = round(min(1.0, rule_total + trust + agent_adj), 4)
    if s.blocked:
        level = "blocked"
    elif final <= thresholds["hands_off_max"]:
        level = "hands_off"
    elif final <= thresholds["supervised_max"]:
        level = "supervised"
    else:
        level = "assisted"
    # Trust earned on other kinds of change never carries over to infrastructure: a real infra
    # change is at least Supervised, whatever the thresholds have widened to.
    ceiling = None
    infra_changes = [c for c in (bundle.get("infra") or {}).get("changes", []) if c.get("type") != "no-op"]
    if level == "hands_off" and (infra_changes or (bundle.get("change_summary") or {}).get("infra_touched")):
        level, ceiling = "supervised", "infrastructure changes are at least Supervised"
    # Same for security: a change that brings in a known advisory, not proven unreachable, never
    # ships hands-off however wide the threshold has grown (a live miss: MR !51 came out at
    # exactly the widened 0.30).
    introduced = [f for f in bundle.get("security") or [] if f.get("new") is not False and f.get("static_reachable") is not False]
    if level == "hands_off" and introduced:
        names = ", ".join(sorted({f"{f.get('cve') or 'advisory'} ({f.get('package')})" for f in introduced}))
        level, ceiling = "supervised", f"a change that adds a known advisory is at least Supervised: {names}"
    return {
        "risk": {
            "rule_score": rule_total,
            "contributions": s.contributions,
            "trust_penalty": trust,
            "trust_items": trust_items,
            "agent_adjustment": agent_adj,
            "agent_note": agent_note,
            "final": final,
        },
        "autonomy": {
            "level": level,
            "blocked_reasons": s.blocked,
            "ceiling": ceiling,
            "thresholds": dict(thresholds),
        },
    }
