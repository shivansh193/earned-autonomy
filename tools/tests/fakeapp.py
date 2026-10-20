"""A tiny in-memory ASGI API that behaves like the target app for replay tests."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qsl

import httpx


class FakeApp:
    def __init__(self, password: str = "superpw", default_limit: int = 100, seed_items: int = 0):
        self.users = {"admin@example.com": password}
        self.tokens: dict[str, str] = {}
        self.items: list[dict] = []
        self.default_limit = default_limit
        for i in range(seed_items):
            self._create("admin@example.com", f"seed-{i}")

    def _create(self, owner: str, title: str) -> dict:
        item = {
            "id": str(uuid.uuid4()),
            "title": title,
            "owner": owner,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.items.insert(0, item)
        return item

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        headers = dict((k.decode().lower(), v.decode()) for k, v in scope["headers"])
        status, payload = self.handle(scope["method"], scope["path"], scope["query_string"].decode(), headers, body)
        data = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": data})

    def handle(self, method, path, query, headers, body):
        q = dict(parse_qsl(query))
        if method == "POST" and path == "/api/v1/login/access-token":
            form = dict(parse_qsl(body.decode()))
            if self.users.get(form.get("username")) != form.get("password"):
                return 400, {"detail": "Incorrect email or password"}
            tok = secrets.token_hex(8)
            self.tokens[tok] = form["username"]
            return 200, {"access_token": tok, "token_type": "bearer"}
        if method == "POST" and path == "/api/v1/users/signup":
            data = json.loads(body)
            if data["email"] in self.users:
                return 400, {"detail": "The user with this email already exists in the system"}
            self.users[data["email"]] = data["password"]
            return 200, {"id": str(uuid.uuid4()), "email": data["email"]}
        auth = headers.get("authorization", "")
        user = self.tokens.get(auth[7:]) if auth.startswith("Bearer ") else None
        if user is None:
            return 401, {"detail": "Not authenticated"}
        if method == "POST" and path == "/api/v1/items/":
            return 200, self._create(user, json.loads(body)["title"])
        if method == "GET" and path == "/api/v1/items/":
            limit = int(q.get("limit", self.default_limit))
            mine = [i for i in self.items if i["owner"] == user]
            return 200, {"data": mine[:limit], "count": len(mine)}
        if path.startswith("/api/v1/items/"):
            iid = path.rsplit("/", 1)[-1]
            for i in self.items:
                if i["id"] == iid and i["owner"] == user:
                    if method == "GET":
                        return 200, i
                    if method == "DELETE":
                        self.items.remove(i)
                        return 200, {"message": "Item deleted successfully"}
            return 404, {"detail": "Item not found"}
        return 404, {"detail": "Not Found"}


def asgi_client(app, base_url: str = "http://testserver") -> httpx.Client:
    """Synchronous httpx client that calls an ASGI app in-process."""

    def handler(request: httpx.Request) -> httpx.Response:
        async def call():
            sent = []
            body = request.read()

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(msg):
                sent.append(msg)

            scope = {
                "type": "http",
                "method": request.method,
                "path": request.url.path,
                "query_string": request.url.query,
                "headers": [(k.lower().encode(), v.encode()) for k, v in request.headers.items()],
            }
            await app(scope, receive, send)
            return sent

        sent = asyncio.run(call())
        start = next(m for m in sent if m["type"] == "http.response.start")
        content = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return httpx.Response(start["status"], headers=start["headers"], content=content)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url=base_url)
