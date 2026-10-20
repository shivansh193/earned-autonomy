"""Trust ledger: append-only JSONL of decisions and their outcomes.

The calibration state (thresholds and per-area penalties) is a pure function of
the ledger, recomputed from the start every time. Anyone can audit how autonomy
grew or shrank by replaying the file.

Calibration rules (``docs/risk-model.md``):

* bad outcome (rollback, incident, failed prod replay) on a **hands-off**
  decision: ``hands_off_max`` drops one step (stricter);
* bad outcome on a **supervised** decision: ``supervised_max`` drops one step;
* any bad outcome adds ``AREA_PENALTY`` to every area the change touched;
* a clean outcome halves the penalty of each area it touched (gone below 0.05);
* ``PROMOTE_AFTER`` consecutive clean hands-off outcomes raise ``hands_off_max``
  one step (autonomy is earned back slowly);
* thresholds stay within floors and ceilings and ``hands_off_max <
  supervised_max``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

INITIAL = {"hands_off_max": 0.2, "supervised_max": 0.5}
STEP = 0.05
FLOORS = {"hands_off_max": 0.05, "supervised_max": 0.3}
CEILINGS = {"hands_off_max": 0.3, "supervised_max": 0.6}
AREA_PENALTY = 0.3
AREA_PENALTY_CAP = 0.6
PROMOTE_AFTER = 5
BAD = {"rollback", "incident", "replay_failed"}
GOOD = {"clean"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def decision_entry(bundle: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "decision",
        "id": uuid.uuid4().hex[:12],
        "ts": _now(),
        "mr_iid": bundle.get("mr_iid"),
        "commit": bundle.get("commit"),
        "pipeline_id": (bundle.get("provenance") or {}).get("pipeline_id"),
        "areas": bundle.get("areas", []),
        "level": decision["autonomy"]["level"],
        "final": decision["risk"]["final"],
        "rule_score": decision["risk"]["rule_score"],
        "thresholds": decision["autonomy"]["thresholds"],
    }


def outcome_entry(decision_id: str, result: str, detail: str = "", seconds: float | None = None) -> dict[str, Any]:
    if result not in BAD | GOOD:
        raise ValueError(f"unknown outcome {result!r}; expected one of {sorted(BAD | GOOD)}")
    e = {"type": "outcome", "decision_id": decision_id, "ts": _now(), "result": result, "detail": detail}
    if seconds is not None:
        e["seconds_to_recover"] = seconds
    return e


def _clamp(name: str, value: float, other: dict[str, float]) -> float:
    value = round(min(max(value, FLOORS[name]), CEILINGS[name]), 4)
    if name == "hands_off_max":
        value = min(value, round(other["supervised_max"] - STEP, 4))
    else:
        value = max(value, round(other["hands_off_max"] + STEP, 4))
    return value


def state(entries: list[dict[str, Any]]) -> dict[str, Any]:
    th = dict(INITIAL)
    penalties: dict[str, float] = {}
    streak = 0
    history = [{"ts": None, "event": "initial", **th}]
    decisions = {e["id"]: e for e in entries if e.get("type") == "decision"}
    counts = {"decisions": len(decisions), "by_level": {}, "outcomes": {}}
    for d in decisions.values():
        counts["by_level"][d["level"]] = counts["by_level"].get(d["level"], 0) + 1

    for e in entries:
        if e.get("type") != "outcome":
            continue
        d = decisions.get(e["decision_id"])
        if d is None:
            continue
        res = e["result"]
        counts["outcomes"][res] = counts["outcomes"].get(res, 0) + 1
        before = dict(th)
        if res in BAD:
            streak = 0
            if d["level"] == "hands_off":
                th["hands_off_max"] = _clamp("hands_off_max", th["hands_off_max"] - STEP, th)
            elif d["level"] == "supervised":
                th["supervised_max"] = _clamp("supervised_max", th["supervised_max"] - STEP, th)
            for a in d.get("areas", []):
                penalties[a] = round(min(penalties.get(a, 0.0) + AREA_PENALTY, AREA_PENALTY_CAP), 4)
        else:
            for a in d.get("areas", []):
                if a in penalties:
                    halved = round(penalties[a] / 2, 4)
                    if halved < 0.05:
                        del penalties[a]
                    else:
                        penalties[a] = halved
            if d["level"] == "hands_off":
                streak += 1
                if streak >= PROMOTE_AFTER:
                    th["hands_off_max"] = _clamp("hands_off_max", th["hands_off_max"] + STEP, th)
                    streak = 0
        if th != before:
            history.append({"ts": e["ts"], "event": f"{res} on {d['level']} (MR !{d.get('mr_iid')})", **th})

    return {
        "thresholds": th,
        "area_penalties": penalties,
        "clean_hands_off_streak": streak,
        "threshold_history": history,
        "counts": counts,
    }
