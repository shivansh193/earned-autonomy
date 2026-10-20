import asyncio
import json
import sys
from pathlib import Path

import pytest

from runtime_hits import HitCounter, HitsEndpoint, resolve_target

SITE = Path(__file__).parent / "fixtures" / "cg" / "site"


@pytest.fixture(autouse=True)
def fake_site(monkeypatch):
    monkeypatch.syspath_prepend(str(SITE))
    for m in [m for m in sys.modules if m.startswith(("fakejwt", "yamlish"))]:
        monkeypatch.delitem(sys.modules, m)


def test_resolve_follows_module_alias_to_method():
    import fakejwt.api_jwt as api

    assert resolve_target("fakejwt.decode") is api.PyJWT.decode.__code__
    assert resolve_target("fakejwt.api_jwt.PyJWT.decode") is api.PyJWT.decode.__code__


def test_counts_only_targets_and_reports_unresolved():
    import fakejwt
    import yamlish

    c = HitCounter(["fakejwt.decode", "fakejwt.algorithms.verify_signature", "yamlish.load", "nope.missing"]).start()
    try:
        for _ in range(3):
            fakejwt.decode("t", "k")
        snap = c.snapshot()
    finally:
        c.stop()
    assert snap["hits"]["fakejwt.decode"] == 3
    assert snap["hits"]["fakejwt.algorithms.verify_signature"] == 3  # transitive callee
    assert snap["hits"]["yamlish.load"] == 0
    assert "nope.missing" in snap["unresolved"]
    yamlish.load("x")  # after stop: not counted, no error
    assert c.snapshot()["hits"]["yamlish.load"] == 0


def test_two_names_for_same_function_both_count():
    import fakejwt

    c = HitCounter(["fakejwt.decode", "fakejwt.api_jwt.PyJWT.decode"]).start()
    try:
        fakejwt.decode("t", "k")
        assert c.snapshot()["hits"] == {"fakejwt.decode": 1, "fakejwt.api_jwt.PyJWT.decode": 1}
    finally:
        c.stop()


def _call(app, method, path, token=None):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)

    headers = [(b"x-ea-token", token.encode())] if token else []
    asyncio.run(app({"type": "http", "method": method, "path": path, "headers": headers}, receive, send))
    status = sent[0]["status"]
    return status, json.loads(sent[1]["body"]) if len(sent) > 1 else None


def test_endpoint_requires_token_and_resets():
    import fakejwt

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    c = HitCounter(["fakejwt.decode"]).start()
    try:
        fakejwt.decode("t", "k")
        app = HitsEndpoint(inner, counter=c, token="s3cret")
        assert _call(app, "GET", "/__ea/hits")[0] == 404
        assert _call(app, "GET", "/__ea/hits", "wrong")[0] == 404
        status, body = _call(app, "GET", "/__ea/hits", "s3cret")
        assert status == 200 and body["hits"]["fakejwt.decode"] == 1
        _call(app, "DELETE", "/__ea/hits", "s3cret")
        assert c.snapshot()["hits"]["fakejwt.decode"] == 0
        assert _call(app, "GET", "/api/v1/items/")[0] == 204  # other paths pass through
    finally:
        c.stop()


def test_from_env(monkeypatch):
    monkeypatch.setenv("EA_HITS_TARGETS", " a.b , c.d ,")
    assert HitCounter.from_env().targets == ["a.b", "c.d"]
    monkeypatch.delenv("EA_HITS_TARGETS")
    assert HitCounter.from_env() is None
