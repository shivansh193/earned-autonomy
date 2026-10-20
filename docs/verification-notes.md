# Verification notes

What we checked in official docs before building on it. Each entry: claim, status, source, date checked.
Status is one of **confirmed**, **contradicted**, **unclear** (docs silent; must test on the hackathon group).

## GitLab Duo Agent Platform (checked 2026-10-20)

| # | Claim | Status | Notes | Source |
|---|---|---|---|---|
| 1 | Custom flows are YAML in the flow registry v1 format | confirmed | Top-level keys: `version: "v1"`, `environment`, `components`, `prompts`, `routers`, `flow.entry_point`. Custom flows support only `environment: ambient`. `name`/`description`/`product_group` are not allowed in custom flows. A `model` field in prompts is not supported. | [custom flow schema](https://docs.gitlab.com/user/duo_agent_platform/flows/custom_flows_schema/), [flow registry v1](https://gitlab.com/gitlab-org/modelops/applied-ml/code-suggestions/ai-assist/-/blob/main/docs/flow_registry/v1.md) |
| 2 | Component types | confirmed (registry) | `AgentComponent` (ReAct loop + tools), `OneOffComponent` (one round of tool calls), `DeterministicStepComponent` (one tool, no LLM), `HumanInputComponent`; built-in `end` / `abort`. | flow registry v1 |
| 3 | Human approval step exists | confirmed in registry; **unclear for ambient custom flows** | `HumanInputComponent` with `interaction_type: approval` (default), `input`, or `external`; output `context:<name>.approval` = `approve`/`reject`. Custom-flows page lists "Human Input" among builder components. Must test that it pauses a triggered ambient flow and how the approver is notified. **Fallback**: CI `when: manual` job / protected-environment deployment approval. | custom flows page, flow registry v1 |
| 4 | Conditional routing | confirmed | `routers: - from: X, condition: {input: "context:...", routes: {value: target, default_route: ...}}` | flow registry v1 |
| 5 | Structured agent output | confirmed in registry; **custom-flows page says `response_schema_id`/`response_schema_version` are NOT supported on `AgentComponent` in custom flows** | So agents must emit JSON in `final_answer` text and we validate it ourselves, or route on plain-text verdicts. | both pages |
| 6 | Trigger types | confirmed | Mention, Assign, Assign reviewer, **Pipeline events (Running / Passed / Failed / Canceled)**, **Merge request (Approved / Created / Marked ready / Merge conflict)**, **Work item (Created / Status changed)**. Configured in **AI > Triggers** by Maintainer/Owner. A per-event goal (≤ 4096 chars) is prepended to the event context. | [triggers](https://docs.gitlab.com/user/duo_agent_platform/triggers/) |
| 7 | Flow-to-flow / bot triggering | **contradicted** | "Only human users can activate triggers." Bots, service accounts and other flows cannot. ⇒ A CI job (project token) mentioning a flow will NOT start it, and a flow can't chain another by mention. Pipeline-event triggers fire on state change of a pipeline a human started (to confirm: does a pipeline started by a scheduled pipeline / bot count?). | triggers page |
| 8 | Pipeline-event context | partly | For pipeline triggers `context:goal` is the full pipeline webhook payload (schema page). Triggers page doesn't specify. | schema page |
| 9 | Flow file location for triggers | confirmed, flag-gated | Trigger "Configuration source" = AI Catalog flow, or "Configuration path" e.g. `.gitlab/duo/flows/claude.yaml` (requires `ai_catalog_create_third_party_flows` flag). | triggers page |
| 10 | Execution config | confirmed | `.gitlab/duo/agent-config.yml`, **read only from the default branch**. Keys seen: `setup_script`, `cache`, `id_tokens`, `network_policy.allowed_domains`. Custom image on a separate page. Runner needs tag `gitlab--duo`; docker/kubernetes executors only. | [execution](https://docs.gitlab.com/user/duo_agent_platform/flows/execution/) |
| 11 | Agent tools | confirmed (partial list) | `read_file`, `create_file_with_contents`, `edit_file`, `list_dir`, `find_files`, `get_repository_file(s)`, `list_repository_tree`, `get_issue`, `get_merge_request`, `create_merge_request_note`, `create_issue`, **`run_command`**, `start_flow`. Full list: `duo_workflow_service/components/tools_registry.py`. The flow can also use the `glab` CLI with `GITLAB_TOKEN` (Bearer, `ai_workflows` scope only). Merge/label/approve tools: **unclear** — check the registry. | flow registry v1, custom flows page |
| 12 | Tier / billing | confirmed | Custom flows GA in 19.2; Free (GitLab.com, with credits), Premium, Ultimate. Triggers need Premium/Ultimate. Usage billed to the flow's service account (no included credits; Monthly Commitment Pool / On-Demand). Model: Claude Sonnet 4 per page. | custom flows + triggers pages |

### Design consequences

1. **Agents cannot be the only glue.** Flows start from human-originated events (MR created, MR approved, pipeline passed/failed on a human-started pipeline). Deterministic CI does the merge/promote/rollback; agents decide and write their decision (MR note + label). CI jobs read the decision.
2. **Structured output** is JSON in the agent's final answer, parsed and schema-validated by a CI job (`tools/risk`), which refuses anything that lowers risk below the rule score.
3. **Human gates**: try `HumanInputComponent` first (day 3). If it can't gate a CI promotion, use GitLab protected-environment deployment approvals (Supervised = 1 approval on `production`; Assisted = manual jobs per step). Both are real GitLab features and visible in the pipeline UI.
4. **Rollback can't be flow-chained**. The monitor/verify CI job does the traffic shift deterministically. The Rollback flow is started by the "pipeline failed" trigger (human-started pipeline) or the scheduled monitor pipeline (to verify whether scheduled pipelines count as human-initiated; owner of the schedule is a human).

## Target app (checked 2026-10-20)

| Claim | Status | Notes |
|---|---|---|
| fastapi/full-stack-fastapi-template is MIT | confirmed | `LICENSE` in repo; README states MIT. Upstream HEAD `b1125d88` (2026-10-15), 1537 commits, imported with full history. |
| Python version | confirmed | 3.14 (`.python-version`, `python:3.14` base image) ⇒ `sys.monitoring` available for runtime hit counts. |
| Auth | confirmed | PyJWT + pwdlib (argon2, bcrypt); `POST /api/v1/login/access-token` (OAuth2 password form). |
| Pagination default | confirmed | `GET /api/v1/items/?skip=0&limit=100` — the scenario-4 change. |

## Hackathon rules (checked 2026-10-20, https://gitlab-transcend.devpost.com/rules)

| Item | Rule |
|---|---|
| Deadline | Submission period ends **2026-10-27 13:00 UTC**. Judging Oct 28 – Nov 14; winners ~Nov 16 14:00 UTC. |
| Path B | Existing open-source project (yours or forked, **MIT**), imported to GitLab, AI post-code automation built around it, **deployed after Oct 5** (Google Cloud preferred). Must include the URL of the original MIT project. Focus: "before" (manual, fragmented) vs "after" (automated, integrated). |
| Repo | Public GitLab URL, MIT license file **detectable in the About section**, all source + instructions. **CI/CD pipeline history visible and showing the automation running.** |
| Live | Path B: public deployed URL, live until ~Nov 16. Google Cloud bonus needs a public live GCP deploy and the deploy code in the repo (max +0.2; final score 1–5.2). |
| Video | **Less than 3 minutes**, public on YouTube, shows the project working, no copyrighted music/third-party marks without permission. |
| Licensing | Our files incl. agent-config YAML are MIT + GitLab DCO 1.1. Third-party components must be OSI-approved; we own compatibility. |
| Judging | Stage 1 pass/fail: fits theme and "reasonably uses GitLab Duo Agent Platform". Stage 2: five equal criteria (Tech Implementation, Design, Impact, Innovation, Presentation). May use "automated AI-driven analysis". Ties broken in criterion order (Tech Implementation first). Judges may judge from text + video only. |
| Prizes | One Path prize + one Special prize per project. Path B: $4k Assisted, $4k Supervised, $5k Hands-off. Specials: Most Stages Covered ($5k, one per Path; "touch the most DevSecOps lifecycle steps ... in the most creative way", referencing about.gitlab.com/topics/devops), Most Environmentally Impactful ($5k, needs SCI-quantified gains), Most Creative ($4k, highest Innovation score, either Path). |
| Onboarding | Register as GitLab Contributor; ~24 business hours to get a group, subgroup, project and onboarding issue. |

## Still to verify

- [x] Hackathon rules page (see above).
- [ ] Which Path B category to enter: the entry spans all three levels; "Best Hands-off" is the target, so the video must show a full hands-off loop first and foremost.
- [ ] HumanInputComponent behaviour in a triggered ambient flow (needs hackathon group access).
- [ ] Whether pipelines started by a schedule fire pipeline-event triggers.
- [ ] Merge / label / approval tools available to flows.
- [ ] Credit cost per flow run.
- [ ] Seeded CVE for scenario 2: realistic vulnerable version of a dependency the app actually calls, with an identifiable function.
