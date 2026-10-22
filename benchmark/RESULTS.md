# Benchmark results

Expectations: [`benchmark/EXPECTED.md`](EXPECTED.md), pushed at commit `77e22ea` (2026-10-22 20:16 UTC)
**before** any benchmark MR was opened. Each case is a draft MR (never merged) that ran the real
pipeline and the Autonomy Router on 2026-10-22, 20:19–20:45 UTC. The Hands-off threshold at the
time was 0.30 (widened twice by clean releases).

## Scorecard (as run, before any fix)

| # | MR | Change | Expected | Got | Risk | What decided it | Result |
|---|---|---|---|---|---|---|---|
| 01 | [!33](../../../merge_requests/33) | docstring | Hands-off | **Hands-off** | 0.00 | no signals; Router: docstring only | ✅ exact |
| 02 | [!34](../../../merge_requests/34) | test only | Hands-off | **Hands-off** | 0.00 | 121 tests passed (one new) | ✅ exact |
| 03 | [!35](../../../merge_requests/35) | 404 copy | Hands-off | **Hands-off** | 0.00 | 62 UI tests passed | ✅ exact |
| 04 | [!36](../../../merge_requests/36) | aria-label | Hands-off | **Hands-off** | 0.00 | no signals | ✅ exact |
| 05 | [!37](../../../merge_requests/37) | token lifetime 8 d → 1 d | Supervised | **Supervised** | 0.35 | Router +0.35: session length for every user, not covered by tests or replay | ✅ exact |
| 06 | [!38](../../../merge_requests/38) | new field in items response | Supervised | **Assisted** | 0.85 | replay: 81 real body diffs; past miss on `GET /items/` +0.15 | ✅ stricter |
| 07 | [!39](../../../merge_requests/39) | page size 100 → 50 | Assisted | **Assisted** | 0.85 | replay body diff; past miss +0.15; Router +0.10 (clients silently get fewer items) | ✅ exact |
| 08 | [!40](../../../merge_requests/40) | check in `get_current_user` | Assisted | **Assisted** | 1.00 | 17 routes touched; Router +0.20 (auth on every route) | ✅ exact |
| 09 | [!41](../../../merge_requests/41) | prod startup probe 40 → 10 | Supervised | run 1: **Assisted**; run 2: **Hands-off** | 0.85 / 0.30 | run 1: the plan lost a state-lock race (missing evidence, +0.60), Router +0.25. Run 2: rules 0.15 + Infra agent 0.15 = 0.30 ≤ the widened threshold | ❌ **miss** (run 2) |
| 10 | [!42](../../../merge_requests/42) | items list ignores ownership | Blocked | **Assisted** | 1.00 | tests passed (none covers it); replay 26 status mismatches incl. a 403 → 200; Router +0.50 (authorization leak) | ❌ **miss** |
| 11 | [!43](../../../merge_requests/43) | "Log In" → "Sign In" | Blocked | **Blocked** | – | Playwright: auth setup failed, 61 UI tests skipped | ✅ exact |
| 12 | [!44](../../../merge_requests/44) | PyJWT floor lowered, 2.3.0 locked | Supervised | **Assisted** | 1.00 | 3 new advisories on the auth path (CVE-2022-29217, …), unmapped so scored conservatively; Router +0.10 | ✅ stricter |

**10 of 12 pass** the pre-registered rules: 8 exact, 2 stricter. **All 4 safe changes came out
Hands-off** (no false alarms). **2 misses**, both on the lenient side, which is the side that matters.

## What the misses taught, and the fixes

- **Case 09: trust leaked across kinds of change.** The Hands-off threshold widened from 0.20 to
  0.30 on clean releases of docs, tooling and small app changes, and one global threshold then
  let a production probe cut through. Fix ([!47](../../../merge_requests/47)): trust earned elsewhere
  never carries over to infrastructure; a real infra change is at least Supervised.
- **Case 10: the evidence saw the leak, the rules didn't block on it.** No test checks that a
  user only sees their own items. The replay did see it: a request the recording saw refused (403)
  now succeeded (200), and the Router called it an authorization leak, but the outcome was Assisted.
  Fix ([!47](../../../merge_requests/47)): a replayed request that was refused (401/403/404) and now
  succeeds is **access widening**, and blocks.
- **Infrastructure hiccup in case 09, run 1**: the MR's terraform plan lost a state-lock race with
  another Terraform run, so the infra evidence was missing (scored conservatively). Fix
  ([!45](../../../merge_requests/45)): MR plans only read state and take no lock.

## After the fixes

Cases 09 and 10 are re-run on top of the fixes; results are added below, alongside (not instead
of) the runs above.

_Pending._
