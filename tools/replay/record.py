"""ASGI middleware that records exchanges to a JSONL traffic file.

Enable it in an app with::

    from replay.record import RecordMiddleware
    app.add_middleware(RecordMiddleware, path="traffic/recorded.jsonl",
                       known_secrets={"SUPERUSER_PASSWORD": settings.FIRST_SUPERUSER_PASSWORD})

Only paths under ``include_prefix`` are recorded. Secrets are replaced with
placeholders before anything touches the disk.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from replay.model import Exchange
from replay.sanitize import sanitize_request_body, sanitize_response

MAX_BODY = 256 * 1024


class RecordMiddleware:
    def __init__(
        self,
        app: Any,
        path: str | Path,
        known_secrets: dict[str, str] | None = None,
        include_prefix: str = "/api/",
    ) -> None:
        self.app = app
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.known_secrets = {k: v for k, v in (known_secrets or {}).items() if v}
        self.include_prefix = include_prefix
        self._lock = threading.Lock()
        self._seq = self._last_seq()
        self._tokens: dict[str, int] = {}  # issued bearer token -> seq of issuing exchange

    def _last_seq(self) -> int:
        if not self.path.exists():
            return 0
        last = 0
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = max(last, json.loads(line)["seq"])
        return last

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.include_prefix):
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []

        async def recv() -> dict:
            msg = await receive()
            if msg["type"] == "http.request":
                chunks.append(msg.get("body", b""))
            return msg

        status = 0
        resp_chunks: list[bytes] = []
        resp_type = ""

        async def snd(msg: dict) -> None:
            nonlocal status, resp_type
            if msg["type"] == "http.response.start":
                status = msg["status"]
                for k, v in msg.get("headers", []):
                    if k.lower() == b"content-type":
                        resp_type = v.decode("latin-1")
            elif msg["type"] == "http.response.body":
                resp_chunks.append(msg.get("body", b""))
            await send(msg)

        start = time.perf_counter()
        await self.app(scope, recv, snd)
        latency = (time.perf_counter() - start) * 1000

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        content_type = headers.get("content-type", "").split(";")[0] or None
        raw = b"".join(chunks)
        body = _decode_request(raw, content_type)
        response = _decode_response(b"".join(resp_chunks), resp_type)

        auth = headers.get("authorization", "")
        auth_ref: int | None = None
        if auth.lower().startswith("bearer "):
            auth_ref = self._tokens.get(auth[7:].strip(), 0)

        with self._lock:
            self._seq += 1
            seq = self._seq
            if isinstance(response, dict) and isinstance(response.get("access_token"), str):
                self._tokens[response["access_token"]] = seq
            ex = Exchange(
                seq=seq,
                method=scope["method"],
                path=scope["path"],
                query=scope.get("query_string", b"").decode("latin-1"),
                content_type=content_type,
                body=sanitize_request_body(body, self.known_secrets),
                auth_ref=auth_ref,
                status=status,
                response=sanitize_response(response),
                latency_ms=round(latency, 2),
            )
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(ex.to_json() + "\n")


def _decode_request(raw: bytes, content_type: str | None) -> Any:
    if not raw:
        return None
    text = raw[:MAX_BODY].decode("utf-8", errors="replace")
    if content_type == "application/json":
        try:
            return json.loads(text)
        except ValueError:
            return text
    if content_type == "application/x-www-form-urlencoded":
        return dict(parse_qsl(text, keep_blank_values=True))
    return text


def _decode_response(raw: bytes, content_type: str) -> Any:
    if not raw:
        return None
    text = raw[:MAX_BODY].decode("utf-8", errors="replace")
    if "json" in content_type:
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text
