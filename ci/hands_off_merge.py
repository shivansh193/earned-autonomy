"""Hands-off merges: the last human step, taken by the system when the change earned it.

Runs on a schedule on main. The merge token never lives in GitLab: it sits in Google
Secret Manager (ea-gitlab-token), readable only by the ea-merger service account, which
only pipelines on main can assume (keyless OIDC federation, attribute.ref == main).
Merge request pipelines, where agent-written code runs, can't read it. An open MR is
merged only when all of these hold:

- it is labelled autonomy::hands-off, is not a draft and GitLab says it is mergeable;
- the pipeline of its current commit passed;
- its newest proof card was posted by the Autonomy Router's own account, for that
  pipeline, and says "Final: hands_off";
- it changes none of the controls: the system never merges changes to itself
  (CI, the flows, the risk engine, the ledger, the infrastructure). Those wait for a person.

Each merge gets a note saying why. Standard library only. Exit 0 even when nothing merges.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

CONTROLS = (".gitlab-ci.yml", "ci/", ".gitlab/", "tools/", ".trust/", "infra/")
# The Autonomy Router flow's own service account, matched exactly (not by prefix).
ROUTER_ACCOUNT = os.environ.get("EA_ROUTER_ACCOUNT", "ai-ea-autonomy-router-gitlab-ai-hackathon")


def api(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    url = os.environ["CI_API_V4_URL"] + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "PRIVATE-TOKEN": os.environ["EA_GITLAB_TOKEN"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def why_not(pid: str, mr: dict) -> str | None:
    """None if the MR may be merged now, else the reason it may not."""
    iid = mr["iid"]
    if mr.get("draft"):
        return "draft"
    _, full = api("GET", f"/projects/{pid}/merge_requests/{iid}")
    pipe = full.get("head_pipeline") or {}
    if pipe.get("status") != "success" or pipe.get("sha") != full.get("sha"):
        return "the current commit has no passed pipeline"
    if full.get("detailed_merge_status") != "mergeable":
        # "unchecked" means GitLab hasn't re-evaluated yet; ask it to, and try next run.
        api("GET", f"/projects/{pid}/merge_requests/{iid}?with_merge_status_recheck=true")
        return f"GitLab says {full.get('detailed_merge_status')}"
    _, notes = api("GET", f"/projects/{pid}/merge_requests/{iid}/notes?sort=desc&order_by=created_at&per_page=50")
    card = next((n for n in notes if "## Autonomy:" in n.get("body", "")), None)
    if card is None or card["author"]["username"] != ROUTER_ACCOUNT:
        return "no proof card from the Autonomy Router"
    # GitLab can run more than one pipeline for the same commit; the card may name any of
    # them, as long as that pipeline passed on the MR's current commit.
    _, pipes = api("GET", f"/projects/{pid}/merge_requests/{iid}/pipelines?per_page=50")
    current = {p["id"] for p in pipes if p.get("sha") == full.get("sha") and p.get("status") == "success"}
    named = {int(x) for x in re.findall(r"Pipeline (\d+)", card["body"])}
    if not (named & current) or "**Final: hands_off**" not in card["body"]:
        return "the newest proof card is not a Hands-off verdict for a passed pipeline of the current commit"
    touched: set[str] = set()
    for page in range(1, 51):  # every changed file, not just the first page
        _, diffs = api("GET", f"/projects/{pid}/merge_requests/{iid}/diffs?per_page=100&page={page}")
        touched |= {p for d in diffs for p in (d["old_path"], d["new_path"]) if p.startswith(CONTROLS)}
        if len(diffs) < 100:
            break
    else:
        return "too many changed files to check; a person merges this"
    touched = sorted(touched)
    if touched:
        return "it changes the system's own controls (" + ", ".join(touched[:4]) + "); a person merges those"
    return None


def token_from_secret_manager() -> str | None:
    """The merge token, fetched keylessly as ea-merger (main pipelines only)."""
    import base64
    from pathlib import Path

    if not (os.environ.get("GITLAB_OIDC_TOKEN") and os.environ.get("EA_MERGER_SA")):
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from risk.gcs import _req, access_token

    os.environ["GCP_DEPLOYER_SA"] = os.environ["EA_MERGER_SA"]
    url = (f"https://secretmanager.googleapis.com/v1/projects/{os.environ['GCP_PROJECT']}"
           "/secrets/ea-gitlab-token/versions/latest:access")
    status, raw, _ = _req(url, headers={"Authorization": f"Bearer {access_token()}"})
    if status != 200:
        print(f"merge token: Secret Manager answered {status}")
        return None
    return base64.b64decode(json.loads(raw)["payload"]["data"]).decode().strip()


def main() -> int:
    if not os.environ.get("EA_GITLAB_TOKEN"):
        token = token_from_secret_manager()
        if token:
            os.environ["EA_GITLAB_TOKEN"] = token
    if not os.environ.get("EA_GITLAB_TOKEN"):
        print("EA_GITLAB_TOKEN is not set: nothing merges without a person")
        return 0
    pid = urllib.parse.quote(os.environ["CI_PROJECT_ID"], safe="")
    _, mrs = api("GET", f"/projects/{pid}/merge_requests?state=opened&target_branch={os.environ.get('CI_DEFAULT_BRANCH', 'main')}"
                        "&labels=autonomy::hands-off&per_page=50")
    if not mrs:
        print("no open Hands-off merge requests")
    for mr in mrs:
        reason = why_not(pid, mr)
        if reason:
            print(f"!{mr['iid']}: not merging: {reason}")
            continue
        status, res = api("PUT", f"/projects/{pid}/merge_requests/{mr['iid']}/merge",
                          {"sha": mr["sha"], "should_remove_source_branch": True})
        if status == 200:
            print(f"!{mr['iid']}: merged ({res.get('merge_commit_sha', '')[:8]})")
            api("POST", f"/projects/{pid}/merge_requests/{mr['iid']}/notes", {"body": (
                "Merged by Earned Autonomy: **Hands-off**. The Autonomy Router's proof card for the current "
                "pipeline says hands_off, the pipeline passed, and the change touches none of the system's "
                f"controls. Merge job: {os.environ.get('CI_JOB_URL', '')}")})
        else:
            print(f"!{mr['iid']}: merge refused ({status}): {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
