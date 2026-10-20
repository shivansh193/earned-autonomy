"""Post the proof card on the MR, updating the previous card instead of adding a
new note every pipeline.

    ea-proofcard post --card proofcard.md

Uses ``EA_GITLAB_TOKEN`` (a project/personal token with ``api`` scope, stored as
a masked CI/CD variable) and the predefined ``CI_API_V4_URL``, ``CI_PROJECT_ID``
and ``CI_MERGE_REQUEST_IID``. Without a token it skips and exits 0: the card is
still on the MR via the job's ``expose_as`` artifact link.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from proofcard.render import MARKER


def upsert_note(client: httpx.Client, project_id: str, mr_iid: str, body: str) -> tuple[str, int]:
    me = client.get("/user").json()["id"]
    page = 1
    existing = None
    while existing is None:
        r = client.get(f"/projects/{project_id}/merge_requests/{mr_iid}/notes",
                       params={"per_page": 100, "page": page, "sort": "asc"})
        r.raise_for_status()
        notes = r.json()
        existing = next((n for n in notes if n["author"]["id"] == me and MARKER in n["body"]), None)
        if not notes or len(notes) < 100:
            break
        page += 1
    if existing:
        r = client.put(f"/projects/{project_id}/merge_requests/{mr_iid}/notes/{existing['id']}", json={"body": body})
        r.raise_for_status()
        return "updated", existing["id"]
    r = client.post(f"/projects/{project_id}/merge_requests/{mr_iid}/notes", json={"body": body})
    r.raise_for_status()
    return "created", r.json()["id"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ea-proofcard")
    sub = p.add_subparsers(dest="cmd", required=True)
    post = sub.add_parser("post")
    post.add_argument("--card", type=Path, required=True)
    a = p.parse_args(argv)

    token = os.environ.get("EA_GITLAB_TOKEN")
    mr = os.environ.get("CI_MERGE_REQUEST_IID")
    if not token or not mr:
        sys.stdout.write("proof card not posted (no EA_GITLAB_TOKEN or not an MR pipeline); see the job artifact\n")
        return 0
    with httpx.Client(base_url=os.environ["CI_API_V4_URL"], headers={"PRIVATE-TOKEN": token}, timeout=30) as c:
        action, note_id = upsert_note(c, os.environ["CI_PROJECT_ID"], mr, a.card.read_text(encoding="utf-8"))
    sys.stdout.write(f"proof card {action} (note {note_id})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
