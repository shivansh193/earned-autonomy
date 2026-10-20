# Demo scenarios

Four scripted changes, each a real MR whose pipeline and Autonomy Router run are kept in the history. Status: ✅ verified, 🧪 verified locally / partly in CI, 🚧 needs the cloud deploy.

## 1. Small safe change → Hands-off ✅ (CI) / 🚧 (deploy)

- **Change**: docs or tooling, or a handler tweak with identical behaviour.
- **Evidence**: tests pass; replay shows 0 diffs; no new findings; no infra.
- **Expected**: risk 0.00, **Hands-off**; the Router posts the proof card and sets `autonomy::hands-off`.
- **Seen**: MR !2 (pipeline 2969421685), risk 0.00, router adjustment +0.00, label `autonomy::hands-off`.

## 2. Vulnerable dependency → evidence closes the noise 🧪

- **Change**: an MR pins `jinja2==3.1.4`. That is the floor the app's own `pyproject.toml` allows (`jinja2>=3.1.4`); the lock currently has 3.1.6.
- **Scanner**: pip-audit/OSV reports **3 advisories** for 3.1.4, all Moderate: CVE-2024-56201, CVE-2024-56326, CVE-2025-27516.
- **Mapped from the fix commits** (`tools/callgraph/advisories.json`):
  - `CodeGenerator.visit_FromImport` (`{% from %}` imports)
  - `SandboxedEnvironment.call` (sandbox only)
  - `filters.do_attr` (`|attr` only)
- **Reachability on this app** (local run, 2026-10-21): **0 of 3 reachable**. `Template.render` *is* reachable from `POST /password-recovery/{email}`, `POST /password-recovery-html-content/{email}` and `POST /users/`. The app doesn't use the sandbox, `|attr` or `{% from %}`. Runtime hits during replay: 0, since the email routes aren't in the recorded traffic.
- **Expected**: the proof card shows "3 scanner findings, 3 deferred with evidence". The Triage flow (🚧) opens a low-priority fix MR back to 3.1.6. The level stays Hands-off unless the Router raises it for the dependency downgrade.
- **Caveat stated on the card**: `visit_FromImport` is reached through dynamic `visit_<Node>` dispatch that static analysis can't follow, so the runtime hit count is the check for that one.
- **Open choice**: if the video should show **Supervised** here, we need a finding that is reachable and exercised, e.g. a vulnerable PyJWT version, since `jwt.decode` runs on every authenticated request. That needs relaxing the `pyjwt>=2.15` constraint, which is less realistic. Decide before recording.

## 3. Risky Terraform → Assisted 🚧

- **Change**: `infra/` replaces the database instance or grants `allUsers` on a non-public resource.
- **Evidence**: `ea-tfplan` marks it risky (+0.60), so the level is **Assisted**. Tested with plan fixtures (`tools/tests/test_tfplan.py`); needs the GCP project for a real plan.

## 4. Behaviour change slips through → rollback → trust tightens 🧪

- **Change**: `read_items` default `limit` 100 → 2. The diff maps to exactly `GET /api/v1/items/`.
- **Staging**: the replay against fresh data shows no diff when staging has too few rows to fill a page, so the change is **Hands-off**.
- **Prod**: the prod replay (real data volume) shows shorter pages, so the Rollback flow shifts traffic back and the ledger records `rollback`.
- **Calibration**: `hands_off_max` 0.20 → 0.15 and `route:GET /api/v1/items/` +0.30, so the next clean `/items` change is **Supervised**.
- **Seen** (local, real backend): exactly this sequence; see [risk-model.md §6](risk-model.md). Needs the cloud deploy for the prod replay and rollback.

## Reproduce

Each scenario is a branch with one commit. Push it, open an MR, and the pipeline plus Router do the rest. Scripts to create the branches: 🚧.
