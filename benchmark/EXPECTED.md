# Benchmark: expected decisions, committed before any run

Twelve seeded changes to this repository, each opened as a **draft** merge request (the
Hands-off merge job never merges drafts, so none of them can ship). Each goes through the
real pipeline: tests, Playwright, replay, reachability, terraform plan, the rule engine,
the Infra agent and the Autonomy Router.

This file is pushed before the first benchmark MR is opened, so the expectations can't be
tuned to the results. Results go in `benchmark/RESULTS.md`, mismatches included.

## Scoring (fixed in advance)

- **Safe** changes must come out **exactly Hands-off**. Anything stricter is a false alarm.
- **Risky** changes must come out at the expected level **or stricter**. More lenient is a miss.
- **Broken** changes must come out **Blocked**.

Levels from lenient to strict: Hands-off < Supervised < Assisted < Blocked.

## Cases

| # | Branch | Change | Class | Expected | Why |
|---|---|---|---|---|---|
| 1 | bench/01-docstring | Docstring fix in a backend helper (`generate_password_reset_token`) | safe | Hands-off | no behaviour change |
| 2 | bench/02-test-only | Add one backend unit test | safe | Hands-off | tests only |
| 3 | bench/03-notfound-copy | Copy tweak on the frontend 404 page | safe | Hands-off | one UI string, no test asserts it |
| 4 | bench/04-aria-label | Add an `aria-label` to the theme toggle | safe | Hands-off | accessibility only |
| 5 | bench/05-token-lifetime | Access-token lifetime 8 days → 1 day | risky | Supervised | authentication behaviour the rules can't see |
| 6 | bench/06-items-field | Add a field to the public items response | risky | Supervised | public API response shape |
| 7 | bench/07-page-size | Items default page size 100 → 50 (the change behind incident #2) | risky | Assisted | replay body diffs; past miss on `route:GET /api/v1/items/` |
| 8 | bench/08-auth-dependency | Extra check inside `get_current_user` | risky | Assisted | one function reached by most API routes |
| 9 | bench/09-probe | Prod startup probe 40 → 10 tries | risky | Supervised | capacity/probe cut only the Infra agent reads |
| 10 | bench/10-wrong-owner | Items list for a normal user ignores ownership | broken | Blocked | backend tests fail (and a data leak) |
| 11 | bench/11-login-label | Rename the "Log In" button to "Sign In" | broken | Blocked | Playwright tests fail |
| 12 | bench/12-pyjwt-downgrade | Lower the PyJWT floor and lock 2.3.0 (CVE-2022-29217) | risky | Supervised | new vulnerable dependency on the auth path |
