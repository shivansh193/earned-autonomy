"""Structural diff of two JSON bodies, ignoring volatile fields."""

from __future__ import annotations

from typing import Any

from replay.values import is_volatile

MAX_DIFFS = 20


def diff(expected: Any, actual: Any, path: str = "$", key: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    _diff(expected, actual, path, key, out)
    return out


def _diff(e: Any, a: Any, path: str, key: str | None, out: list[dict[str, Any]]) -> None:
    if len(out) >= MAX_DIFFS:
        return
    if is_volatile(key, e) and is_volatile(key, a):
        return
    if isinstance(e, dict) and isinstance(a, dict):
        for k in sorted(set(e) | set(a)):
            if k not in a:
                out.append({"path": f"{path}.{k}", "kind": "missing", "expected": _short(e[k])})
            elif k not in e:
                out.append({"path": f"{path}.{k}", "kind": "added", "actual": _short(a[k])})
            else:
                _diff(e[k], a[k], f"{path}.{k}", k, out)
        return
    if isinstance(e, list) and isinstance(a, list):
        if len(e) != len(a):
            out.append({"path": path, "kind": "length", "expected": len(e), "actual": len(a)})
        for i, (ev, av) in enumerate(zip(e, a, strict=False)):
            _diff(ev, av, f"{path}[{i}]", key, out)
        return
    if type(e) is not type(a):
        out.append({"path": path, "kind": "type", "expected": _short(e), "actual": _short(a)})
    elif e != a:
        out.append({"path": path, "kind": "value", "expected": _short(e), "actual": _short(a)})


def _short(v: Any) -> Any:
    if isinstance(v, str) and len(v) > 120:
        return v[:117] + "..."
    if isinstance(v, (dict, list)):
        text = repr(v)
        return text if len(text) <= 120 else text[:117] + "..."
    return v
