import json

import httpx

from proofcard.post import main, upsert_note
from proofcard.render import MARKER


def fake_gitlab(notes):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": 9})
        if req.method == "GET":
            return httpx.Response(200, json=notes)
        if req.method == "PUT":
            return httpx.Response(200, json={"id": int(req.url.path.rsplit("/", 1)[1])})
        body = json.loads(req.content)
        notes.append({"id": 100 + len(notes), "body": body["body"], "author": {"id": 9}})
        return httpx.Response(201, json=notes[-1])

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://gl/api/v4"), calls


def test_creates_then_updates_own_card():
    notes = [{"id": 1, "body": MARKER + " someone else's", "author": {"id": 5}}]
    c, calls = fake_gitlab(notes)
    assert upsert_note(c, "1", "7", MARKER + "\ncard v1")[0] == "created"
    action, nid = upsert_note(c, "1", "7", MARKER + "\ncard v2")
    assert (action, nid) == ("updated", 101)
    assert ("PUT", "/api/v4/projects/1/merge_requests/7/notes/101") in calls


def test_skips_without_token(tmp_path, monkeypatch, capsys):
    card = tmp_path / "c.md"
    card.write_text("x")
    monkeypatch.delenv("EA_GITLAB_TOKEN", raising=False)
    assert main(["post", "--card", str(card)]) == 0
    assert "not posted" in capsys.readouterr().out
