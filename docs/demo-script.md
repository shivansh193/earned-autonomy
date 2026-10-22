# Demo scenarios

Each scenario is a real MR whose pipeline, Autonomy Router review and (after merge) Cloud Run rollout are kept in the project history. Status: ✅ ran live on GitLab and Google Cloud, 🧪 verified locally only.

## 1. Small safe change → Hands-off ✅

- **Change**: a configuration or tooling change with no behavioural effect.
- **Evidence**: tests pass, the replay shows no diffs, no new findings, and any infra change is not risky.
- **What happened**:
  - MR [!5](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/5) (Cloud Run timeouts) and MR [!7](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/7) (pipeline fix) were labelled `autonomy::hands-off`.
  - After merge, each was deployed to staging and **promoted to production with no human click**; for !7 the post-deploy monitor then ran clean.
  - MRs !2 and !3 were Hands-off too, before deploys existed.

## 2. Vulnerable dependency → evidence closes the noise 🧪

- **Change**: an MR pins `jinja2==3.1.4`, the floor the app's own `pyproject.toml` allows; the lock has 3.1.6.
- **Scanner**: pip-audit/OSV reports **3 advisories** for 3.1.4, all Moderate: CVE-2024-56201, CVE-2024-56326 and CVE-2025-27516.
- **Mapped from the fix commits** (`tools/callgraph/advisories.json`):
  - CVE-2024-56201 → `CodeGenerator.visit_FromImport` (`{% from %}` imports)
  - CVE-2024-56326 → `SandboxedEnvironment.call` (sandbox only)
  - CVE-2025-27516 → `filters.do_attr` (`|attr` only)
- **Reachability on this app** (local, 2026-10-21): **0 of 3 reachable**.
  - `Template.render` *is* reachable, from `POST /password-recovery/{email}`, `POST /password-recovery-html-content/{email}` and `POST /users/`.
  - The app doesn't use the sandbox, `|attr` or `{% from %}`.
  - Caveat: `visit_FromImport` is reached through dynamic `visit_<Node>` dispatch that static analysis can't follow, so runtime hits are the check for that one.
- **Still to run live**: the MR itself, and the Triage flow opening the fix MR.

## 3. Risky infrastructure → Assisted ✅

- **Change**: MR [!4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/4) created the Cloud Run services themselves.
- **Evidence**: `terraform plan` (keyless auth) flagged two `allUsers` invoker grants (public access) and a production service at min instances 0. That scored +0.60, so the level was **Assisted**.
- **What happened**: a person merged it. Staging deployed automatically, and production waited for a manual approval, which was given, and was then created. Only the approved environment changed; staging's deploy never touched prod.

## Agent judgement in the loop ✅

- **Change**: MR [!6](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/6) added the monitor, auto-rollback and the GCS ledger.
- **Rules**: 0.15 → Hands-off.
- **Autonomy Router: +0.10 → Supervised**, citing the specific files. The change adds code that "automatically re-pins prod Cloud Run traffic", grants the deployer a new secret, and adds a ledger "that feeds future autonomy decisions"; the rollback path had not yet run live.
- **What happened**: one human approval promoted it, and the monitor ran clean.

## 4. A change ships against the warning → caught in prod → rollback → trust drops, then recovers ✅

The original plan was a change that slips through every check. It didn't: CI caught it.

- A first version (default page size 100 → 20) was caught by the CI replay: the admin listing went 36 → 20 items.
- The second version (→ 50) was caught through the API contract: `openapi.json` showed the documented default changed.
- Both were scored **Assisted** (rules 0.60, Router +0.10, with a precise client-impact explanation).

What ran live after that:

1. **Shipped anyway.** MR [!8](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/8) was merged and its production promotion approved *despite* the Assisted warning. That's a human override, which happens in real teams.
2. **Caught in prod.** The post-deploy monitor gave the new revision (`api-prod-00005`) and the previous one (`00004`) private tagged URLs and replayed the canary account's traffic against both on the same production data. The canary owns 150 items in prod (5 in staging). Result: `GET /api/v1/items/` returned **50 items instead of 100**.
3. **Rolled back automatically in 3.3 s.** Traffic was pinned back to `00004`. The job failed, which started the **Rollback agent**: it opened incident [#2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/issues/2) with the evidence and linked it from MR !8.
4. **Trust dropped where it failed.** The ledger in GCS recorded `rollback` against the decision's areas, so `route:GET /api/v1/items/` got a **+0.30** penalty.
5. **The next change was held.** The revert MR [!9](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/9) was perfectly clean (0 status mismatches, 0 body diffs), yet came out **Supervised, risk 0.30**. Its proof card shows exactly one reason: "Trust ledger: past miss in `route:GET /api/v1/items/` +0.30".
6. **Trust earned back.** !9 was approved and promoted. The monitor compared it with the last *good* revision (`00004`), skipping the rolled-back `00005`, and found it clean. The penalty **halved to 0.15**.

## Reproduce

Each scenario is an MR. Push a branch, open the MR, and the pipeline plus the Router do the rest. After merge, the promotion follows the `autonomy::` label. Scripts to recreate the scenario branches: 🚧.
