# Earned Autonomy tools

Deterministic evidence collectors. They run as CI jobs and write JSON that the Duo agents read. MIT licensed, all new code.

| Tool | Command | Output |
|---|---|---|
| Static reachability | `ea-callgraph reach` / `batch` / `routes` | `{cve, package, function, reachable, routes, paths, possible_paths, parse_errors}` |
| Traffic replay | `ea-replay run` | `{requests, endpoints, status_mismatches, body_diffs, p95_ms_before, p95_ms_after, per_endpoint, *_details}` |
| Runtime hits | *(day 2)* | per-function hit counts during replay |
| Terraform plan | *(day 2)* | blast radius, risky changes, cost delta |
| Risk engine | *(day 2)* | rule score, level, thresholds |
| Proof card | *(day 2)* | MR note markdown |

## Setup

The target app is Python 3.14, and the callgraph must parse it with a Python at least as new as the app:

```bash
uv venv -p 3.14 tools/.venv314
uv pip install -p tools/.venv314 -e "tools[dev]"
tools/.venv314/Scripts/python -m pytest tools   # bin/ on Linux
```

## ea-callgraph

```bash
ea-callgraph reach --app-root backend --app-package app \
  --site-packages backend/.venv/lib/python3.14/site-packages --dep-package jwt \
  --function jwt.decode --cve CVE-XXXX-YYYY --package pyjwt
```

It answers whether the vulnerable function is reachable from any HTTP route.

* `reachable: true` means there is a resolved call path, with one shortest path per route.
* `"possible"` means the only link is a call on a receiver of unknown type that has the same method name. We report it instead of hiding it.
* `"unknown"` means no path was found but a module failed to parse. We never claim "unreachable" on incomplete evidence.
* `false` means no path from any route.

It resolves imports, including relative imports and re-exports, and module-level aliases (`decode = _obj.decode`). It infers instance types from assignments, `with` statements, parameter annotations and `self`, and follows inheritance. It also understands FastAPI `Depends(...)` and `Annotated[T, Depends(f)]` dependency aliases, and rebuilds route paths from `APIRouter(prefix=...)` and `include_router(..., prefix=...)`.

Limits: it doesn't track dynamic dispatch through containers or callbacks, `getattr`, or monkey-patching. That is what runtime hit counts are for.

## ea-replay

```bash
EA_PASSWORD=... ea-replay run --traffic traffic/recorded.jsonl \
  --target https://api-staging... [--baseline https://api-prod...] \
  --secret SUPERUSER_PASSWORD=env:EA_PASSWORD --out reports/replay.json
```

**Recording.** `replay.record.RecordMiddleware` is ASGI middleware. It writes one JSON line per request and replaces secrets with `${NAME}` placeholders before writing. It drops tokens from responses and links each authenticated request to the exchange that issued its token (`auth_ref`).

**Replaying.** The replayer handles values that legitimately change between runs:

* It logs in again for each token the recording used, and uses that live token.
* It maps recorded IDs to live IDs from responses and rewrites them into later paths, queries and bodies.
* It gives signup emails (`*@replay.example.com`) a per-run suffix, so replays can be repeated against the same database.
* It fills secrets from `--secret`.

**Diffing.** The comparison happens in recorded space. ID-like fields and volatile fields (timestamps, tokens) are never compared by value. Every remaining diff is labelled `unclassified`, and the Validation agent decides whether it is a real regression or noise.

With `--baseline`, the same traffic is replayed against two live deployments (prod vs candidate), and they are compared to each other instead of to the recording.

Exit code: 1 if there are status mismatches, otherwise 0.
