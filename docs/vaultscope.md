# VaultScope: the same loop on an unrelated app

VaultScope is an IPsec VPN analysis tool with a FastAPI backend (350 tests, 22 routes),
unrelated to the FastAPI template. Its code is public at
[github.com/shivansh193/vaultscope](https://github.com/shivansh193/vaultscope). The pipeline,
tools and Duo flows from this project ran on it in a private GitLab project in the hackathon
group (GitLab project 87957974), so the merge requests themselves aren't public. These are their
proof cards, as posted by the Autonomy Router on 2026-10-23.

| MR | Change | Tests | Rule engine | Router | Final |
|---|---|---|---|---|---|
| !2 | CSV export of a report | 352 passed | Hands-off, 0.00 | +0.00 | **Hands-off**, 0.00 |
| !3 | authentication on the report endpoints | 352 passed | Hands-off, 0.10 | raised, citing the token and HMAC lines; planning prior from its issue applied | **Supervised**, 0.40 |
| !4 | "tidy-up" of CEF log escaping that dropped the `=` escape (log injection in the SIEM export) | 349 passed, **1 failed** | Blocked | posted on the failed pipeline | **Blocked** |

Also on VaultScope: Jinja2 3.1.4's 3 advisories were **0 of 3 reachable** from its 22 routes,
checked against the templates.

The integration is the same files as here, vendored: `.gitlab-ci.yml`, `ci/*.sh`, `tools/`,
`.gitlab/duo/flows/`, and `traffic/recorded.jsonl` (42 recorded exchanges).
