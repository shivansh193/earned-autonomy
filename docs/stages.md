# Stage map

What runs at each GitLab DevSecOps stage, who decides, and where it lives. ✅ = built and running, 🚧 = next.

| Stage | What runs | Decision maker | Where | Status |
|---|---|---|---|---|
| **Plan** | Planning flow on *work item created*: blast-radius estimate and risk prior | Planning agent | `.gitlab/duo/flows/` | 🚧 |
| **Create** | Changes from people or Duo agents; Triage flow opens fix MRs | Triage agent | `.gitlab/duo/flows/` | 🚧 |
| **Secure** | Locked deps → `pip-audit` → **static reachability** of each vulnerable function from HTTP routes (call graph with FastAPI `Depends` resolution). Target branch audited too, so only findings the MR introduces are scored. | deterministic; Triage agent issues per-CVE verdicts | `dependency-audit-reachability` job, `ci/secure.sh`, `tools/callgraph` | ✅ (jobs) / 🚧 (agent) |
| **Verify** | App test suite (58 tests) and tool tests in the MR test widget. **Replay of recorded real traffic** against this commit's app + fresh Postgres, with **runtime hit counts** on the vulnerable functions found in Secure. | deterministic; Validation agent labels each diff real or noise | `test`, `replay-recorded-traffic` jobs, `tools/replay`, `tools/runtime_hits` | ✅ (jobs) / 🚧 (agent) |
| **Package** | Container image, Artifact Registry, SBOM | deterministic | — | 🚧 (after cloud choice) |
| **Configure** | Terraform plan → blast radius + cost delta | Infra agent | `infra/`, `tools/tfplan` | 🚧 |
| **Release** | Evidence bundle → **risk score** → **autonomy level** (Hands-off / Supervised / Assisted / Blocked) → **proof card** on the MR | rule engine (floor) + **Autonomy Router agent** (may only raise risk; trigger: Pipeline events → Passed) posts the card and sets `autonomy::<level>` | `autonomy-decision` job, `ci/release-decide.sh`, `tools/risk`, `tools/proofcard`, `.gitlab/duo/flows/autonomy-router.yaml` | ✅ (rules, card, router) / 🚧 (promotion) |
| **Monitor** | Post-deploy prod replay + health; rollback by traffic shift on failure, incident issue | Rollback agent | — | 🚧 |
| **Govern** | Audit record per decision; **trust ledger** recalibrates thresholds and per-area penalties | deterministic calibration | `.trust/ledger.jsonl`, `tools/risk/ledger.py` | ✅ (logic) / 🚧 (recording from CI) |

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
