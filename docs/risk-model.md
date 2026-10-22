# Risk model

How a change gets its autonomy level, how that level is earned or lost over time, and what the system will never do. The implementation is `tools/risk/engine.py` (scoring) and `tools/risk/ledger.py` (calibration). The tests in `tools/tests/test_risk.py` pin every rule below.

## 1. Evidence

One **evidence bundle** per pipeline (`ea-risk bundle`), built from deterministic collectors:

| Evidence | Collector | Notes |
|---|---|---|
| Tests | the app's own pytest suite, JUnit XML | |
| Scope | `git diff -U0` mapped to changed functions, then to the HTTP routes that can reach them (`tools/callgraph`) | Function-level scope. A hunk in module-level code falls back to the whole file. |
| Security | dependency findings, then static reachability (`ea-callgraph batch`), then runtime hits during replay (`tools/runtime_hits`) | A finding counts only if it is **new in this MR** (not on the target branch). Pre-existing ones are listed on the card and handled by the Triage flow's fix MR. |
| Behaviour | replay of recorded real traffic (`ea-replay`), then the Validation agent's per-diff verdicts | A diff without a verdict counts as real. |
| Infra | `terraform show -json` parsed for blast radius and cost (`tools/tfplan`) | |
| Areas | the routes (and infra resources) the change touches, e.g. `route:GET /api/v1/items/` | The unit of trust. |

## 2. Rule score

Points add up. Each one appears as a line on the proof card.

| Rule | Points |
|---|---|
| New finding, statically reachable **and** executed during replay (or no runtime data): high/critical | +0.50 |
| …same, medium/low | +0.30 |
| New finding, statically reachable, **0 runtime hits** | +0.15 |
| New finding, only "possibly" reachable (untyped receiver) | +0.15 |
| New finding, not reachable | 0 (deferred, with the evidence on the card) |
| Replay regression: any status mismatch or real body diff | +0.60 |
| Replay p95 latency up more than 20% | +0.20 |
| No replay evidence at all | +0.30 |
| Infra: replace/destroy of a stateful resource, IAM or public-access change | +0.60 |
| Infra: other changes | +0.15 |
| Infra: cost delta above $5/month | +0.10 |
| Infra files changed but no plan evidence | +0.60 |
| Routes touched beyond 2 | +0.05 each |
| Planning agent's prior (capped) | up to +0.30 |
| **Trust ledger**: past misses in the areas this change touches (capped) | up to +0.60 |
| **Infra agent** adjustment (infra changes the plan rules can't see: capacity, timeouts, probes, env/secret wiring, replacements) | 0 to +0.30 |
| **Autonomy Router agent** adjustment | ≥ 0 only |

**Blocked** (no promotion at any level): failing tests, or no test results.

## 3. Levels

| Final risk | Level | What happens |
|---|---|---|
| ≤ `hands_off_max` (start 0.20) | **Hands-off** | merge, deploy, verify, promote, no human |
| ≤ `supervised_max` (start 0.50) | **Supervised** | one MR approval, with the proof card as evidence |
| above | **Assisted** | approval at each step |

## 4. Safety properties

1. **The agent can only raise risk.** A negative adjustment is recorded on the card and ignored, so the deterministic score is a floor no model output can lower.
2. **Missing evidence counts against the change**: no replay, no runtime data, no plan for changed infra, no test results.
3. **Failing tests block** at every level.
4. **The state is a pure function of the ledger.** Thresholds and penalties are recomputed from the append-only `ledger.jsonl` every time, so anyone can audit how autonomy was earned or lost by replaying the file.

## 5. Earning and losing autonomy (calibration)

The ledger records every decision and, later, its outcome: `clean`, `rollback`, `incident` or `replay_failed`.

| Event | Effect |
|---|---|
| Bad outcome on a Hands-off decision | `hands_off_max` −0.05 |
| Bad outcome on a Supervised decision | `supervised_max` −0.05 |
| Any bad outcome | +0.30 penalty on every area the change touched (cap 0.60) |
| Clean outcome | halves the penalty of each area it touched (dropped below 0.05) |
| 5 consecutive clean Hands-off outcomes | `hands_off_max` +0.05 |
| Bounds | `hands_off_max` ∈ [0.05, 0.30], `supervised_max` ∈ [0.30, 0.60], `hands_off_max` < `supervised_max` |

**Why per-area trust.** A clean change scores about 0. Tightening only the global threshold after a miss would not stop the *next* change to the same code from shipping hands-off. The area penalty does: autonomy is withdrawn where the system was wrong and kept everywhere else.

## 6. Worked example (scenario 4, run on the real backend, 2026-10-20)

1. `read_items` default `limit` 100 → 2. The diff maps to exactly `GET /api/v1/items/`. Tests pass (58/58). The replay against staging shows no diff, because staging has too little data to fill a page. Risk **0.00**, **Hands-off**.
2. The prod replay catches the shorter pages. The Rollback flow shifts traffic back, and the ledger records `rollback` (41 s to recover in the smoke run).
3. Calibration: `hands_off_max` 0.20 → **0.15**; `route:GET /api/v1/items/` penalty **+0.30**.
4. The next, equally clean change to `GET /api/v1/items/`: risk **0.30**, **Supervised**, and the proof card says why ("Trust ledger: past miss in `route:GET /api/v1/items/`"). A change to `GET /api/v1/users/me` still ships Hands-off.
