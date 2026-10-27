# See it for yourself

Ten minutes, no account needed. Each claim below has one link that shows it happening, and
every link is a real run on this project (GitLab Transcend "Life After Code", Path B, the 46k-star
[fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)).

**What it is:** every merge request gets evidence, a risk score and an autonomy level
(Hands-off, Supervised, Assisted, Blocked). Duo agents can only raise the risk. After each
release, production replays real traffic against the new and previous revisions, rolls back
on any difference, and writes the outcome to a trust ledger that recalibrates autonomy per area.

## The claims

| # | Claim | Proof |
|---|---|---|
| 1 | **An agent-written fix reached production with no human step.** A vulnerable dependency landed on `main`; the Triage agent opened the fix, the system re-ran its checks, the Router rated it Hands-off, the scheduled job merged it, and it was promoted and monitored | [MR !52](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/52): advisory on `main` 03:24, fix opened by the agent 03:32, merged 03:54, [promoted](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/jobs/17490954710) 03:59, [monitor clean](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/jobs/17490954711) 04:01 (UTC, Oct 27) |
| 2 | **Zero clicks from MR to prod** when a change earns it: opened 19:42, Hands-off 19:47, merged by the system 19:54, promoted 19:58, monitor clean 20:02 | [MR !31](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/31) |
| 3 | **Production has the last word:** a change shipped against an Assisted warning returned 50 items instead of 100 in prod and was rolled back in 3.3 s | [monitor job](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/jobs/17364968751) |
| 4 | **Trust is remembered per area:** the next, perfectly clean change to that route was held at Supervised for exactly one reason, the past miss | [MR !9 proof card](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/9) |
| 5 | **Trust is earned back:** penalties halve with clean releases, and the Hands-off threshold widened 0.20 → 0.25 → 0.30 on its own | [trust ledger page](https://showcase-4900ab.gitlab.io/) |
| 6 | **Agents catch what rules can't:** reviewing the auto-merge code itself, the Router found three security flaws, all fixed before merge | [MR !22](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/22) |
| 7 | **Measured, not claimed:** 12 seeded changes, expected levels committed before any run; 10/12 as run, misses published and fixed, then 12/12. A live miss on Oct 27 (a known advisory shipped hands-off) was published and fixed within the hour | [benchmark/RESULTS.md](benchmark/RESULTS.md) |
| 8 | **Evidence costs only what it can tell you:** a documentation-only MR skips the tests, replay and browser tests it can't change (DOCS_SAVING), and a code change with missing evidence is still Blocked | DOCS_MR_LINK |

## Every stage, with a real run

| Stage | What runs | Run |
|---|---|---|
| Plan | Planning agent scores an issue's blast radius before code exists, sets `risk-prior::` | [issue #3](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/issues/3) |
| Create | people, and the Triage agent's fix MRs | [MR !52](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/52) (Triage agent) |
| Secure | dependency audit + static reachability of each vulnerable function + runtime hits; Triage verdict per advisory | [MR !12](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/12) |
| Verify | 58 backend tests, 62 Playwright UI tests, replay of 166 recorded real requests on a fresh database | [MR !23](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/23) |
| Package | image built on MRs that change it, pushed to Artifact Registry after merge | any `main` pipeline |
| Configure | Terraform plan at attribute level, cost and carbon delta; Infra agent review | [MR !16](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/16) |
| Release | evidence bundle → risk → level → proof card; Autonomy Router; promotion routed by level | [MR !31](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/31) |
| Monitor | canary replay of new vs previous prod revision, automatic rollback, Rollback agent opens the incident | [incident #2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/issues/2) |
| Govern | trust ledger in GCS, calibration, public ledger page, CI footprint per release | [ledger page](https://showcase-4900ab.gitlab.io/) |

## Sustainability

- **Docs MRs skip what they can't change:** DOCS_SAVING_LONG
- **CI footprint per release** on the ledger page (Cloud Carbon Footprint method; runner 2 vCPU / 8 GiB).
- **Carbon delta on every infrastructure change**, on the proof card ([MR !16](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/16)).
- **Production scales to zero** between requests.

## What people did, and what they didn't

So you don't have to dig: a person wrote the code of this system, merged every MR that changes
the system's own controls (CI, flows, tools, ledger, infrastructure: the merge job refuses those
by design), approved Supervised and Assisted releases, placed the merge token in Secret Manager,
and deliberately shipped the Assisted change in claim 3 and the vulnerable pin in claim 1 to
create the situations. Everything else in claims 1, 2 and 5 happened with no human action.

## Where things are

- Source of truth: this project. Mirror: https://github.com/shivansh193/earned-autonomy
- Live app: https://api-prod-gkozang2ta-uc.a.run.app (scales to zero; the first request takes ~25 s)
- Rules and calibration: [docs/risk-model.md](docs/risk-model.md) · stage map: [docs/stages.md](docs/stages.md)
