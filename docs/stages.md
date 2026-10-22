# Stage map

What runs at each GitLab DevSecOps stage, who decides, and where it lives. ✅ = built and running, 🚧 = next.

| Stage | What runs | Decision maker | Where | Status |
|---|---|---|---|---|
| **Plan** | Planning flow on *work item created*: blast-radius estimate and risk prior | Planning agent | `.gitlab/duo/flows/` | 🚧 |
| **Create** | Changes from people, and fix MRs opened by the Triage agent (which then go through the same loop) | people; Triage agent | `.gitlab/duo/flows/triage-agent.yaml` | ✅ |
| **Secure** | Locked deps → `pip-audit` → **static reachability** of each vulnerable function from HTTP routes (call graph with FastAPI `Depends` resolution). Target branch audited too, so only findings the MR introduces are scored. | deterministic evidence; **Triage agent** (Pipeline events → Passed) issues a verdict per advisory (fix now / fix soon / review / defer with evidence) and opens a **fix MR** from CI-verified `uv lock --upgrade-package` edits | `dependency-audit-reachability` job, `ci/secure.sh`, `tools/callgraph` (incl. `fixes.py`), `.gitlab/duo/flows/triage-agent.yaml` | ✅ |
| **Verify** | App test suite (58 tests) and tool tests in the MR test widget. **Replay of recorded real traffic** against this commit's app + fresh Postgres, with **runtime hit counts** on the vulnerable functions found in Secure. | deterministic; Validation agent labels each diff real or noise | `test`, `replay-recorded-traffic` jobs, `tools/replay`, `tools/runtime_hits` | ✅ (jobs) / 🚧 (agent) |
| **Package** | Backend image (`infra/Dockerfile.api`) pushed to Artifact Registry by digest | deterministic | `package-image` job | ✅ (SBOM 🚧) |
| **Configure** | `terraform plan` on MRs touching `infra/` (keyless auth) → `ea-tfplan`: risky changes + cost delta → risk evidence | rules; Infra agent 🚧 | `configure-plan` job, `ci/plan.sh`, `infra/`, `tools/tfplan` | ✅ |
| **Release** | Evidence bundle → **risk score** → **autonomy level** (Hands-off / Supervised / Assisted / Blocked) → **proof card** on the MR | rule engine (floor) + **Autonomy Router agent** (may only raise risk; trigger: Pipeline events → Passed) posts the card and sets `autonomy::<level>` | `autonomy-decision` job, `ci/release-decide.sh`, `tools/risk`, `tools/proofcard`, `.gitlab/duo/flows/autonomy-router.yaml` | ✅ (rules, card, router, staging deploy, prod promotion routed by `autonomy::` label: automatic for Hands-off, manual approval otherwise) |
| **Monitor** | After every prod promotion: canary replay of the **new revision vs the previous one on the same prod data** (tagged revision URLs, no traffic moved); any difference → **automatic rollback** (traffic pinned to previous, time to recover measured) → job fails → **Rollback agent** opens an incident issue and links it from the MR | deterministic rollback; Rollback agent (Pipeline events → Failed) | `monitor-prod` job, `tools/monitor`, `traffic/canary.jsonl`, `infra/seed_canary.py`, `.gitlab/duo/flows/rollback-agent.yaml` | ✅ (built; first live rollback pending) |
| **Govern** | Every prod release appends its decision + outcome to the **trust ledger in GCS** (conditional writes, no lost updates); every MR decision **reads** it, so thresholds and per-area penalties follow real outcomes; proof card = audit record | deterministic calibration | `tools/risk/ledger.py`, `tools/risk/gcs.py`, `gs://…-tfstate/trust/ledger.jsonl` | ✅ |

## Pipeline (`.gitlab-ci.yml`)

```
secure   dependency-audit-reachability ──┐
verify   test (needs: [])                 ├──> release  autonomy-decision
         replay-recorded-traffic ◄────────┘             (bundle → score → level → proof card)
            (needs reachability: knows which functions to count)
```

The order is secure → verify on purpose: the replay's hit counter watches exactly the functions the reachability job found.

## Platform facts this design rests on

All verified on the hackathon project on 2026-10-20; see [verification-notes.md](verification-notes.md).

- Flows can comment, label, open MRs and issues, and run shell commands. They **cannot merge, approve, trigger pipelines or deploy**, so those stay in CI, and agents hand decisions to CI through labels and notes.
- `HumanInputComponent` pauses fine, but resuming after Approve failed (KeyError). Human gates therefore use **GitLab MR approvals** and the *Merge request: Approved* trigger.
- Flows are triggered only by human-originated events (mention, MR events, pipeline events, work items).
