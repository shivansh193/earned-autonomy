# Earned Autonomy

**Autonomy isn't a setting. It's earned, per change.** Every merge request gets evidence, a risk score and an autonomy level, and every autonomous decision ships with its proof.

A [GitLab Transcend "Life After Code"](https://gitlab-transcend.devpost.com/) entry (Path B). It's built around the MIT-licensed [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template), whose backend API is the system under test.

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
 deploy/monitor (🚧): promote by level, prod replay, rollback ─► trust ledger ─► calibration
```

## Stages

Full map: [docs/stages.md](docs/stages.md). ✅ running, 🚧 next.

| Stage | What runs | Who decides |
|---|---|---|
| Plan | Planning flow: blast radius, risk prior 🚧 | Planning agent |
| Create | MRs from people and agents; Triage flow opens fix MRs 🚧 | Triage agent |
| Secure | dependency audit + **static reachability** of each vulnerable function ✅ | rules; Triage agent 🚧 |
| Verify | 58 app tests + **replay of 166 recorded real requests** against a fresh database, with **runtime hit counts** ✅ | rules; Validation agent 🚧 |
| Package | image, registry, SBOM 🚧 | — |
| Configure | Terraform plan → blast radius, risky changes, cost delta (parser ✅, GCP plan 🚧) | rules; Infra agent 🚧 |
| Release | bundle → risk → level → **proof card**; **Autonomy Router flow** ✅ | rule floor + Router agent |
| Monitor | prod replay + health, rollback by traffic shift 🚧 | Rollback agent |
| Govern | audit record per decision; **trust ledger + calibration** (logic ✅, CI recording 🚧) | deterministic |

## See it run

- **MR [!2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/2)**: the pipeline in action. Secure, verify and release jobs ran green, then the Autonomy Router posted the proof card (risk 0.00, adjustment +0.00) and set `autonomy::hands-off`.
- **MR [!1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43529136/showcase/-/merge_requests/1)**: the platform probe. What we verified about Duo flows before building on them ([docs/verification-notes.md](docs/verification-notes.md)).
- The four demo scenarios and their status: [docs/demo-script.md](docs/demo-script.md).

Live URLs: 🚧 (Google Cloud Run deploy in progress).

## Results so far (real runs only)

| Measure | Value | Where |
|---|---|---|
| Recorded traffic | 166 requests, 19 endpoints, every status class (200/400/401/403/404/422), no secrets on disk | `traffic/recorded.jsonl` |
| Replay on a fresh database (every ID different) | 0 status mismatches, 0 body diffs | CI `replay-recorded-traffic` |
| Behaviour regression caught | pagination default 100 → 2: 8 body diffs, 0 status mismatches (tests alone would pass it) | local run, [risk-model.md §6](docs/risk-model.md) |
| Static reachability on the real app | `jwt.decode` reachable from 18 of 23 routes via `Depends(get_current_user)` | `ea-callgraph` |
| Runtime hits during one replay | `PyJWT.decode` 135, `verify_and_update` 10, `PasswordHash.hash` 8 | `runtime_hits` |
| Scanner noise (scenario 2, local) | Jinja2 3.1.4: **3 advisories → 0 reachable** from any route | [demo-script.md](docs/demo-script.md) |
| Trust calibration | after one hands-off rollback on `/items`: `hands_off_max` 0.20 → 0.15, next `/items` change → Supervised | `tools/risk/ledger.py`, tests |
| Duo credits | about 0.6 per flow run | GitLab Credits dashboard |
| Tool tests | 54 passing | `tools/tests` |

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
- Not built yet: deploy/monitor/rollback (Google Cloud), Triage, Validation, Infra and Planning flows, and ledger recording from CI.

## License and attribution

- MIT ([LICENSE](LICENSE)). The application is © 2019 Sebastián Ramírez, from [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) (MIT); its original README is in [docs/upstream-template-README.md](docs/upstream-template-README.md).
- All Earned Autonomy code (`tools/`, `ci/`, `.gitlab-ci.yml`, `.gitlab/duo/`, `docs/`) is new and MIT-licensed.
- Third-party tools used at runtime: [pip-audit](https://github.com/pypa/pip-audit) (Apache-2.0), [uv](https://github.com/astral-sh/uv) (MIT/Apache-2.0), [httpx](https://github.com/encode/httpx) (BSD-3).
