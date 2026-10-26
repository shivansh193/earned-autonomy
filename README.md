# Earned Autonomy

**Autonomy isn't a setting. It's earned, per change.** Every merge request gets evidence, a risk score and an autonomy level, and every autonomous decision ships with its proof.

A [GitLab Transcend "Life After Code"](https://gitlab-transcend.devpost.com/) entry (Path B), applied to the MIT-licensed [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) (46k GitHub stars): its FastAPI backend, React frontend and Terraform-managed Cloud Run infrastructure all go through the loop.

## Judge TL;DR: verify it in 5 minutes

| Claim | Where to see it |
|---|---|
| Trust is calibrated **per area** from what production did, and it moves both ways | [Trust ledger page](https://showcase-4900ab.gitlab.io/): `route:GET /api/v1/items/` +0.30 after a rollback, halved by the next clean release; the Hands-off threshold widened 0.20 → 0.25 → 0.30, one step per five clean Hands-off releases |
| **Zero clicks from MR to prod** when the change earned it | MR [!31](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/31): opened 19:42, Router said Hands-off 19:47, [merged by the scheduled job](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/jobs/17395211268) 19:54, [promoted to prod](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/pipelines/2978178092) 19:58, [monitor kept it](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/jobs/17395296068) 20:02 (UTC, Oct 22). The system never merges changes to its own controls (CI, flows, tools, ledger, infra) |
| A change shipped against the warning was **caught in prod and rolled back in 3.3 s** | MR [!8](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/8) → [monitor log](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/jobs/17364968751) → incident [#2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/issues/2) opened by the Rollback agent |
| The next clean change to that route was **held** because of that miss | MR [!9](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/9) proof card: Supervised, one reason, "past miss in `route:GET /api/v1/items/` +0.30" |
| Agents only **raise** risk, with reasons a rule can't see | [!6](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/6) (Router +0.10), [!16](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/16) (Infra agent: timeout and probe cuts the plan rules saw as plain updates), [!22](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/22) (reviewing the auto-merge code itself, the Router found three security flaws: a prefix match on the trusted account, only the first 100 files checked for controls, an unchecked merge status treated as mergeable; all fixed before merge) |
| Decisions are **measured, not claimed** | [benchmark/RESULTS.md](benchmark/RESULTS.md): 12 seeded changes, expected levels committed *before* any run. 10/12 as run (all 4 safe changes Hands-off; both misses on the lenient side), published with the fixes they led to (an infrastructure ceiling; access widening blocks), then 12/12 |
| Security findings are scored by **reachability**, not severity | [!12](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/12) (Triage agent opened the fix MR); on a second codebase, Jinja2's 3 advisories: 0 reachable |
| Work is risk-scored **before code exists** | issue [#3](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/issues/3) (Planning agent's blast-radius estimate and `risk-prior::` label) |
| The whole app is tested and deployed: **62 Playwright UI tests** and 58 backend tests on every MR, replay of 166 real requests, the image built on MRs that change it, canary replay and a UI check in prod. The UI tests caught a real bug before prod: the build baked in the dev `.env`'s `localhost:8000` API address | any MR pipeline, e.g. [!23](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/23) |
| It's not tied to one app | VaultScope, an unrelated FastAPI app (350 tests): Hands-off, Supervised (planning prior applied) and **Blocked** (a failing test) verdicts, live |
| Sustainability | each release's CI footprint (about 7 minutes, 0.6 g CO2e) and the carbon delta of every infra change, on the ledger page and proof cards; prod scales to zero |

Live app: https://api-prod-gkozang2ta-uc.a.run.app (first request after idle takes ~25 s: scale to zero).

## The problem

Every DevSecOps stage has tooling, but small teams rarely let automation ship to production on its own, because nothing tells them which automated decisions are safe. Automation is either always manual or always automatic. Nothing decides **per change, with evidence**, and nothing **earns trust over time**.

## The idea

For each change:

1. **Collect evidence**:
   - Is a vulnerable function reachable from an HTTP route, and does real traffic execute it?
   - Does replaying recorded real traffic change behaviour?
   - What does the infrastructure plan touch and cost?
   - Do the tests pass?
2. **Score risk** with documented, itemised rules. An agent may **raise** the score for risks the rules can't see, and can never lower it.
3. **Pick a level**:
   - **Hands-off**: ship with no human.
   - **Supervised**: one approval, with the proof card.
   - **Assisted**: approval at each step.
   - **Blocked**.
4. **Learn**. A trust ledger records whether each decision held up. A rollback in an area tightens autonomy *in that area*; clean deploys earn it back.

```
MR / pipeline events
      │
      ▼
 secure:  pip-audit ─► static reachability (call graph, FastAPI Depends) ─┐
 verify:  tests ─┐   replay recorded traffic + runtime hits on vulns ◄────┘
                 ▼
 release: evidence bundle ─► rule score (floor) ─► level ─► proof card
                 ▼
          Autonomy Router agent (Duo flow, pipeline passed): reviews diff, may only RAISE risk,
          posts the card, sets autonomy::<level>
                 ▼
 deploy/monitor: promote by level, prod replay, rollback ─► trust ledger ─► calibration
```

## Stages

Full map: [docs/stages.md](docs/stages.md). ✅ running, 🚧 next.

| Stage | What runs | Who decides |
|---|---|---|
| Plan | **Planning agent**: blast-radius estimate + bounded risk prior when an issue is created ✅ | Planning agent |
| Create | MRs from people, and fix MRs opened by the Triage agent ✅ | people; Triage agent |
| Secure | dependency audit + **static reachability** of each vulnerable function; **Triage agent** verdict per advisory ✅ | rules; Triage agent |
| Verify | 58 app tests + **replay of 166 recorded real requests** against a fresh database, with **runtime hit counts** ✅ | rules; Validation agent 🚧 |
| Package | backend image → Artifact Registry ✅ | deterministic |
| Configure | `terraform plan` on MRs touching `infra/` → risky changes, cost delta, attribute-level diff; **Infra agent** judges what the rules can't see ✅ | rules; Infra agent |
| Release | bundle → risk → level → **proof card**; **Autonomy Router flow**; after merge, staging deploy and prod promotion **routed by level** (automatic / manual approval) ✅ | rule floor + Router agent |
| Monitor | prod replay of the canary account against the new and previous revisions, **automatic rollback** by traffic shift, incident issue ✅ | rules; Rollback agent |
| Govern | audit record per decision; **trust ledger in GCS + calibration**, recorded by the monitor after every release, published as a [public page](https://showcase-4900ab.gitlab.io/) ✅ | deterministic |

## See it run

- **MR [!2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/2)**: the pipeline in action. Secure, verify and release jobs ran green, then the Autonomy Router posted the proof card (risk 0.00, adjustment +0.00) and set `autonomy::hands-off`.
- **MR [!1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/1)**: the platform probe. What we verified about Duo flows before building on them ([docs/verification-notes.md](docs/verification-notes.md)).
- The four demo scenarios and their status: [docs/demo-script.md](docs/demo-script.md).
- **Trust ledger, live**: https://showcase-4900ab.gitlab.io/ (every release, its level and outcome, each area's penalty over time; regenerated after every release).
- **On a second codebase**: the same pipeline and agents run on VaultScope, an unrelated FastAPI app (350 tests, 22 routes), in a private project in the same group. Results below.

**Live (Google Cloud Run, scale to zero, so the first request after a quiet period takes ~25 s):**

- Production API: https://api-prod-gkozang2ta-uc.a.run.app/docs
- Staging API: https://api-staging-gkozang2ta-uc.a.run.app/docs
- MR [!4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/4): the infrastructure itself went through the loop. The Terraform plan grants public access, so the Router labelled it **Assisted**; a person merged it and approved the production job.

## Results so far (real runs only)

| Measure | Value | Where |
|---|---|---|
| Recorded traffic | 166 requests, 19 endpoints, every status class (200/400/401/403/404/422), no secrets on disk | `traffic/recorded.jsonl` |
| Replay on a fresh database (every ID different) | 0 status mismatches, 0 body diffs | CI `replay-recorded-traffic` |
| Behaviour regression caught | pagination default 100 → 2: 8 body diffs, 0 status mismatches (tests alone would pass it) | local run, [risk-model.md §6](docs/risk-model.md) |
| Static reachability on the real app | `jwt.decode` reachable from 18 of 23 routes via `Depends(get_current_user)` | `ea-callgraph` |
| Runtime hits during one replay | `PyJWT.decode` 135, `verify_and_update` 10, `PasswordHash.hash` 8 | `runtime_hits` |
| **Agent-opened security fix (live)** | certifi `PYSEC-2026-4320` → Triage verdict → fix MR !12 opened by the agent; its lock change is byte-identical to `uv lock --upgrade-package` | [demo-script.md §2](docs/demo-script.md) |
| Scanner noise (local) | Jinja2 3.1.4: **3 advisories → 0 reachable** from any route (and the app's FastAPI won't even install that version) | [demo-script.md §2](docs/demo-script.md) |
| **Planning estimate (live)** | issue #3 (optional `sort` parameter on the items list): the Planning agent read the route modules, posted its blast-radius estimate and set `risk-prior::0.00` + `planned` | issue #3 |
| **Production rollback (live)** | MR !8 shipped against an Assisted warning; the prod monitor caught `GET /items/` returning 50 instead of 100 items (canary, prod data) and **rolled back in 3.3 s**; the Rollback agent opened incident #2 | [demo-script.md §4](docs/demo-script.md) |
| **Trust lost and earned back (live)** | `route:GET /api/v1/items/` penalty +0.30 after the rollback, so the clean revert MR !9 was held at **Supervised** (risk 0.30); its clean release halved the penalty to 0.15 | GCS ledger, MR !9 proof card |
| **Autonomy earned (live)** | five clean Hands-off releases in a row (!11, !12, !14, !18, !19) widened the Hands-off threshold from **0.20 to 0.25**, and the next five (to !28) to **0.30**, with no human changing a setting | [ledger page](https://showcase-4900ab.gitlab.io/) |
| **Zero-click release (live)** | MR !31 went from opened to live in prod in 20 minutes with no human action: Router review, scheduled Hands-off merge, automatic prod promotion, clean monitor. The merge token is read from Secret Manager by an identity only `main` pipelines can assume | [MR !31](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/31) |
| **Frontend through the loop (live)** | the React app is built into the deployed image; 62/62 Playwright tests on every MR; the prod monitor checks the page, root element and JS bundle on the new and previous revision | MR !23 |
| **Infra agent (live)** | MR !16 cut the prod request timeout 120 s → 30 s and the startup probe 40 → 10 tries. The plan rules saw two plain updates (0.15, Hands-off). The Infra agent flagged both, plus that the apply would strip the monitor's rollback revision tags: **+0.20 → Supervised**. A first version double-counted with the Router (0.55); fixed so infra findings are counted once | MR !16 |
| **Blocked (live, VaultScope !4)** | a "tidy-up" of CEF log escaping dropped the `=` escape (log injection in the SIEM export); 1 of 350 tests failed, the engine said **Blocked**, and the Router posted it on the failed pipeline | VaultScope !4 |
| **Second codebase (live, VaultScope)** | Jinja2 3.1.4's 3 advisories: **0 of 3 reachable** from 22 routes (verified against the templates); a report CSV export came out Hands-off (0.00); report auth came out Supervised, the Router citing the token and HMAC lines | VaultScope !2, !3 |
| Releases through the loop (live) | Hands-off auto-promoted to prod: MRs !5, !7, !11, !12, !14, !15, !17, !18, !19, !20, !21, !24, !28, !31 · Supervised (Router raised +0.10): MR !6 · Assisted: MRs !4 (public infra), !8 | MR history |
| Duo credits | about 0.6 per flow run | GitLab Credits dashboard |
| Tool tests | 74 passing | `tools/tests` |

The headline numbers for the video get filled in from the full scenario runs, and only from them.

## How it works

| Piece | Path | Doc |
|---|---|---|
| Pipeline | `.gitlab-ci.yml`, `ci/*.sh` | [stages.md](docs/stages.md) |
| Static reachability | `tools/callgraph` | [tools/README.md](tools/README.md) |
| Record/replay + diff | `tools/replay` | [tools/README.md](tools/README.md) |
| Runtime hit counter | `tools/runtime_hits` | [tools/README.md](tools/README.md) |
| Terraform plan analysis | `tools/tfplan` | [tools/README.md](tools/README.md) |
| Risk engine, ledger, calibration | `tools/risk` | [risk-model.md](docs/risk-model.md) |
| Proof card | `tools/proofcard` | [risk-model.md](docs/risk-model.md) |
| Duo flows | `.gitlab/duo/flows/` | [verification-notes.md](docs/verification-notes.md) |

**Safety properties.** The rule score is a floor that agents can only raise. Missing evidence counts against a change. Failing tests block at every level. The calibration state is a pure function of an append-only ledger.

## Run it

```bash
# tools (Python 3.14 to match the app)
uv venv -p 3.14 tools/.venv314 && uv pip install -p tools/.venv314 -e "tools[dev]"
tools/.venv314/Scripts/python -m pytest tools      # bin/ on Linux

# app + Postgres, recording traffic
docker run -d --name ea-postgres -e POSTGRES_PASSWORD=changethis -e POSTGRES_DB=app -p 5434:5432 postgres:18
uv sync --package app && cd backend
DATABASE_URL=postgresql://postgres:changethis@localhost:5434/app alembic upgrade head
DATABASE_URL=postgresql://postgres:changethis@localhost:5434/app EA_RECORD_FILE=../traffic/recorded.jsonl \
  fastapi run app/main.py --port 8010 --workers 1
```

In GitLab, every MR runs the pipeline. With the `ea-autonomy-router` flow enabled on *Pipeline events → Passed*, the Router posts the proof card.

## Limitations (honest)

- Static reachability doesn't follow dynamic dispatch (`getattr`, visitor patterns, callbacks); runtime hits are the check. The tool reports "possible" or "unknown" instead of claiming unreachable when it can't see.
- The advisory → vulnerable-function map is curated by hand from fix commits; unmapped advisories are scored conservatively.
- Replay compares against a recording; latency is only scored against a baseline run in the same environment.
- GitLab Duo `HumanInputComponent` pauses but did not resume after approval in our tests, so human gates use GitLab MR approvals.
- Hands-off merges use the project owner's personal access token (participants are Developers, so no project or group tokens). It lives in Google Secret Manager, readable only from `main` pipelines; GitLab shows the owner as the merger, and the job leaves a note saying the system merged it.
- GitLab starts no flow for pipelines a bot started, so an agent-opened MR gets its Router review once a person re-runs its pipeline.
- Not built yet: the Validation agent. CI job tokens can't read an MR's linked issues, so on a private project the Router applies the Planning agent's prior instead of the release job (it is listed separately on the card). The first ledger entry (MR !6) was recorded before the enforced-level fix and shows the rule level (hands_off) instead of the enforced Supervised; the ledger is append-only, so it stays as written.

## License and attribution

- MIT ([LICENSE](LICENSE)). The application is © 2019 Sebastián Ramírez, from [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) (MIT); its original README is in [docs/upstream-template-README.md](docs/upstream-template-README.md).
- All Earned Autonomy code (`tools/`, `ci/`, `.gitlab-ci.yml`, `.gitlab/duo/`, `docs/`) is new and MIT-licensed.
- Third-party tools used at runtime: [pip-audit](https://github.com/pypa/pip-audit) (Apache-2.0), [uv](https://github.com/astral-sh/uv) (MIT/Apache-2.0), [httpx](https://github.com/encode/httpx) (BSD-3).
