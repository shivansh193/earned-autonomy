import json

import pytest

from replay.model import load
from replay.record import RecordMiddleware
from replay.runner import Login, compare, replay
from replay.values import endpoint_template, fill_placeholders

from .fakeapp import FakeApp, asgi_client

PW = "superpw"
LOGIN = Login("/api/v1/login/access-token", "admin@example.com", PW)


def record_session(tmp_path, app):
    """Drive a realistic session through the recording middleware."""
    path = tmp_path / "traffic.jsonl"
    rec = RecordMiddleware(app, path, known_secrets={"SUPERUSER_PASSWORD": PW})
    c = asgi_client(rec)
    tok = c.post(LOGIN.path, data={"username": "admin@example.com", "password": PW}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    created = [c.post("/api/v1/items/", json={"title": f"t{i}"}, headers=h).json() for i in range(3)]
    c.get("/api/v1/items/", headers=h)
    c.get(f"/api/v1/items/{created[1]['id']}", headers=h)
    c.delete(f"/api/v1/items/{created[0]['id']}", headers=h)
    c.get(f"/api/v1/items/{created[0]['id']}", headers=h)  # 404 after delete
    # a second user signs up and logs in with their own (non-superuser) password
    c.post("/api/v1/users/signup", json={"email": "alice@replay.example.com", "password": "alicepw1"})
    tok2 = c.post(LOGIN.path, data={"username": "alice@replay.example.com", "password": "alicepw1"}).json()["access_token"]
    c.post("/api/v1/items/", json={"title": "alice item"}, headers={"Authorization": f"Bearer {tok2}"})
    c.get("/api/v1/items/", headers={"Authorization": f"Bearer {tok2}"})
    return path


def test_recording_has_no_secrets_and_links_tokens(tmp_path):
    path = record_session(tmp_path, FakeApp(PW))
    text = path.read_text()
    assert PW not in text and "alicepw1" not in text
    exs = load(path)
    assert exs[0].body == {"username": "admin@example.com", "password": "${SUPERUSER_PASSWORD}"}
    assert exs[0].response["access_token"] == "<redacted>"
    assert exs[1].auth_ref == exs[0].seq
    alice_login = next(e for e in exs if e.body and e.body.get("username") == "alice@replay.example.com")
    assert alice_login.body["password"].startswith("${secret:")
    assert exs[-1].auth_ref == alice_login.seq


def test_replay_against_fresh_db_is_clean(tmp_path):
    exs = load(record_session(tmp_path, FakeApp(PW)))
    target = FakeApp(PW)  # a different database: every ID differs
    run = replay(exs, "http://t", secrets={"SUPERUSER_PASSWORD": PW}, client=asgi_client(target))
    report = compare(exs, run)
    assert report["requests"] == len(exs)
    assert report["status_mismatches"] == 0, report["status_mismatch_details"]
    assert report["body_diffs"] == 0, report["body_diff_details"]


def test_replay_is_repeatable_on_same_db(tmp_path):
    exs = load(record_session(tmp_path, FakeApp(PW)))
    target = FakeApp(PW)
    for _ in range(2):  # signup email is uniquified per run, so no "already exists"
        run = replay(exs, "http://t", secrets={"SUPERUSER_PASSWORD": PW}, client=asgi_client(target))
        assert compare(exs, run)["status_mismatches"] == 0


def test_changed_pagination_default_is_caught_only_with_enough_data(tmp_path):
    """Scenario 4: default limit 100 -> 2. With little data the replay can't see it."""
    exs = load(record_session(tmp_path, FakeApp(PW)))
    small = FakeApp(PW, default_limit=2)
    report = compare(exs, replay(exs, "http://t", secrets={"SUPERUSER_PASSWORD": PW}, client=asgi_client(small)))
    assert report["status_mismatches"] == 0
    diffs = report["body_diff_details"]
    assert diffs and all(d["endpoint"] == "GET /api/v1/items/" for d in diffs)
    kinds = {x["kind"] for d in diffs for x in d["diffs"]}
    assert "length" in kinds
    assert diffs[0]["classification"] == "unclassified"


def test_baseline_mode_compares_two_live_deployments(tmp_path):
    exs = load(record_session(tmp_path, FakeApp(PW)))
    secrets = {"SUPERUSER_PASSWORD": PW}
    base = replay(exs, "http://prod", secrets=secrets, client=asgi_client(FakeApp(PW, seed_items=5)))
    same = replay(exs, "http://stg", secrets=secrets, client=asgi_client(FakeApp(PW, seed_items=5)))
    assert compare(exs, same, base)["body_diffs"] == 0
    changed = replay(exs, "http://stg", secrets=secrets, client=asgi_client(FakeApp(PW, seed_items=5, default_limit=3)))
    assert compare(exs, changed, base)["body_diffs"] > 0


def test_token_from_before_recording_uses_login(tmp_path):
    app = FakeApp(PW)
    c = asgi_client(app)
    tok = c.post(LOGIN.path, data={"username": "admin@example.com", "password": PW}).json()["access_token"]
    path = tmp_path / "t.jsonl"
    rec_client = asgi_client(RecordMiddleware(app, path))
    rec_client.post("/api/v1/items/", json={"title": "x"}, headers={"Authorization": f"Bearer {tok}"})
    exs = load(path)
    assert exs[0].auth_ref == 0
    run = replay(exs, "http://t", login=LOGIN, client=asgi_client(FakeApp(PW)))
    assert run.results[exs[0].seq].status == 200


def test_unknown_placeholder_fails_loudly():
    with pytest.raises(KeyError):
        fill_placeholders("${SUPERUSER_PASSWORD}", {})
    a = fill_placeholders("${secret:abc}", {})
    assert a == fill_placeholders("${secret:abc}", {}) and len(a) > 12


def test_endpoint_template():
    assert endpoint_template("GET", "/api/v1/items/3fa85f64-5717-4562-b3fc-2c963f66afa6") == "GET /api/v1/items/{id}"
    assert endpoint_template("POST", "/api/v1/password-recovery/a@b.com") == "POST /api/v1/password-recovery/{email}"


def test_cli_exit_code(tmp_path, monkeypatch):
    from replay import cli, runner

    exs_path = record_session(tmp_path, FakeApp(PW))
    target = FakeApp(PW)
    real = runner.replay

    def fake_replay(exs, url, **kw):
        return real(exs, url, client=asgi_client(target), **kw)

    monkeypatch.setattr(cli, "replay", fake_replay)
    out = tmp_path / "r.json"
    rc = cli.main(["run", "--traffic", str(exs_path), "--target", "http://t",
                   "--secret", f"SUPERUSER_PASSWORD={PW}", "--out", str(out)])
    assert rc == 0
    assert json.loads(out.read_text())["status_mismatches"] == 0
