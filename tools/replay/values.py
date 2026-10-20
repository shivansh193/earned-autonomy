"""Dynamic values: things that legitimately differ between the recording and a
replay (IDs, tokens, timestamps, run-unique emails) and must be rewritten in
requests and ignored or normalised in responses."""

from __future__ import annotations

import hashlib
import re
from typing import Any

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
UUID_ANY_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_:.-]+)\}")

ID_KEYS = {"id", "access_token", "refresh_token", "token"}
VOLATILE_KEYS = {"created_at", "updated_at", "timestamp", "date", "expires_at", "access_token"}


def is_id_like(key: str | None, value: Any) -> bool:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return False
    if isinstance(value, str) and UUID_RE.match(value):
        return True
    return key is not None and (key in ID_KEYS or key.endswith("_id"))


def is_volatile(key: str | None, value: Any) -> bool:
    if key is not None and key in VOLATILE_KEYS:
        return True
    return isinstance(value, str) and bool(ISO_TS_RE.match(value))


class ValueMap:
    """Bidirectional map recorded value <-> live value for one replay run."""

    def __init__(self) -> None:
        self.fwd: dict[str, str] = {}
        self.inv: dict[str, str] = {}

    def add(self, recorded: Any, live: Any) -> None:
        if recorded is None or live is None or recorded == live:
            return
        r, lv = str(recorded), str(live)
        if len(r) < 4:  # avoid rewriting tiny ints like "1" everywhere
            return
        self.fwd[r] = lv
        self.inv[lv] = r

    def learn(self, recorded: Any, live: Any, key: str | None = None) -> None:
        """Walk recorded and live response bodies in parallel and record ID pairs."""
        if isinstance(recorded, dict) and isinstance(live, dict):
            for k, v in recorded.items():
                if k in live:
                    self.learn(v, live[k], k)
        elif isinstance(recorded, list) and isinstance(live, list):
            for rv, lv in zip(recorded, live, strict=False):
                self.learn(rv, lv, key)
        elif is_id_like(key, recorded) and is_id_like(key, live):
            self.add(recorded, live)

    # ---- rewriting -------------------------------------------------------
    def to_live(self, value: Any) -> Any:
        return _rewrite(value, self.fwd)

    def to_recorded(self, value: Any) -> Any:
        return _rewrite(value, self.inv)


def _rewrite(value: Any, mapping: dict[str, str]) -> Any:
    if not mapping:
        return value
    if isinstance(value, dict):
        return {k: _rewrite(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v, mapping) for v in value]
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        # embedded values, e.g. IDs inside a path
        for old in sorted(mapping, key=len, reverse=True):
            if old in value:
                value = value.replace(old, mapping[old])
        return value
    if isinstance(value, int) and not isinstance(value, bool) and str(value) in mapping:
        mapped = mapping[str(value)]
        return int(mapped) if mapped.isdigit() else mapped
    return value


def fill_placeholders(value: Any, secrets: dict[str, str]) -> Any:
    """Replace ``${NAME}`` placeholders. Unknown ``secret:<hash>`` placeholders get a
    deterministic strong dummy so that e.g. signup-then-login stays consistent."""

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in secrets:
            return secrets[name]
        if name.startswith("secret:"):
            return "Ea-" + hashlib.sha256(name.encode()).hexdigest()[:16] + "-pw!"
        raise KeyError(f"no value for placeholder ${{{name}}}")

    if isinstance(value, dict):
        return {k: fill_placeholders(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [fill_placeholders(v, secrets) for v in value]
    if isinstance(value, str):
        return PLACEHOLDER_RE.sub(sub, value)
    return value


def endpoint_template(method: str, path: str) -> str:
    """``GET /api/v1/items/3fa8...`` -> ``GET /api/v1/items/{id}``."""
    parts = []
    for seg in path.split("/"):
        if UUID_RE.match(seg) or seg.isdigit():
            parts.append("{id}")
        elif "@" in seg:
            parts.append("{email}")
        else:
            parts.append(seg)
    return f"{method} {'/'.join(parts)}"
