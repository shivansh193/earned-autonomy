from __future__ import annotations

import hmac
import importlib
import inspect
import json
import os
import sys
import threading
from types import CodeType
from typing import Any

PATH = "/__ea/hits"
TOOL_NAME = "earned-autonomy-hits"


def resolve_target(qualname: str) -> CodeType:
    """``pkg.module.Class.method`` -> the function's code object.

    Tries the longest importable module prefix, then walks attributes. Follows
    ``__wrapped__`` (decorators) and bound methods; module-level aliases such as
    ``decode = _obj.decode`` resolve to the underlying function.
    """
    parts = qualname.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj: Any = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            obj = inspect.getattr_static(obj, attr) if inspect.isclass(obj) else getattr(obj, attr)
            if isinstance(obj, (staticmethod, classmethod)):
                obj = obj.__func__
        obj = inspect.unwrap(getattr(obj, "__func__", obj))
        code = getattr(obj, "__code__", None)
        if code is None:
            raise TypeError(f"{qualname} is not a Python function (got {type(obj).__name__})")
        return code
    raise ImportError(f"cannot import any module prefix of {qualname}")


class HitCounter:
    def __init__(self, targets: list[str]) -> None:
        self.targets = list(dict.fromkeys(targets))
        self.counts: dict[str, int] = {t: 0 for t in self.targets}
        self.errors: dict[str, str] = {}
        self._by_code: dict[CodeType, list[str]] = {}
        self._lock = threading.Lock()
        self._tool_id: int | None = None

    @classmethod
    def from_env(cls) -> HitCounter | None:
        raw = os.environ.get("EA_HITS_TARGETS", "").strip()
        return cls([t.strip() for t in raw.split(",") if t.strip()]) if raw else None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> HitCounter:
        if not hasattr(sys, "monitoring"):
            raise RuntimeError("runtime hit counting needs Python 3.12+ (sys.monitoring)")
        for t in self.targets:
            try:
                self._by_code.setdefault(resolve_target(t), []).append(t)
            except Exception as exc:  # report, don't crash the app
                self.errors[t] = f"{type(exc).__name__}: {exc}"
        mon = sys.monitoring
        for tool_id in range(mon.PROFILER_ID, 6):
            if mon.get_tool(tool_id) is None:
                mon.use_tool_id(tool_id, TOOL_NAME)
                self._tool_id = tool_id
                break
        else:
            raise RuntimeError("no free sys.monitoring tool id")
        mon.register_callback(self._tool_id, mon.events.PY_START, self._on_start)
        for code in self._by_code:
            mon.set_local_events(self._tool_id, code, mon.events.PY_START)
        return self

    def stop(self) -> None:
        if self._tool_id is None:
            return
        mon = sys.monitoring
        for code in self._by_code:
            mon.set_local_events(self._tool_id, code, 0)
        mon.register_callback(self._tool_id, mon.events.PY_START, None)
        mon.free_tool_id(self._tool_id)
        self._tool_id = None

    def _on_start(self, code: CodeType, offset: int) -> Any:
        names = self._by_code.get(code)
        if names:
            with self._lock:
                for n in names:
                    self.counts[n] += 1
        return None

    # ---------------------------------------------------------------- output
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "hits": dict(self.counts),
                "unresolved": dict(self.errors),
                "active": self._tool_id is not None,
            }

    def reset(self) -> None:
        with self._lock:
            for k in self.counts:
                self.counts[k] = 0


class HitsEndpoint:
    """ASGI middleware serving ``GET/DELETE /__ea/hits`` behind a shared token."""

    def __init__(self, app: Any, counter: HitCounter, token: str) -> None:
        if not token:
            raise ValueError("HitsEndpoint needs a non-empty token")
        self.app = app
        self.counter = counter
        self.token = token.encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] != PATH:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        given = headers.get(b"x-ea-token", b"")
        if not hmac.compare_digest(given, self.token):
            await _send_json(send, 404, {"detail": "Not Found"})
            return
        if scope["method"] == "DELETE":
            self.counter.reset()
        await _send_json(send, 200, self.counter.snapshot())


async def _send_json(send: Any, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})
