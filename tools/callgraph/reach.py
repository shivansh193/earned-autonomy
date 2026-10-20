"""Reachability queries over an :class:`Index`."""

from __future__ import annotations

from collections import deque
from typing import Any

from callgraph.index import Index, Route


def _matches(index: Index, node: str, targets: set[str]) -> bool:
    if node in targets:
        return True
    return any(node.startswith(t + ".") for t in targets)


def _shortest_path(index: Index, start: str, targets: set[str]) -> list[str] | None:
    prev: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        if _matches(index, cur, targets):
            path = [cur]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])  # type: ignore[arg-type]
            return list(reversed(path))
        func = index.functions.get(cur)
        if func is None:
            continue
        for nxt in sorted(func.calls):
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    return None


def _reachable_functions(index: Index, start: str) -> dict[str, str | None]:
    prev: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        func = index.functions.get(cur)
        if func is None:
            continue
        for nxt in func.calls:
            if nxt not in prev:
                prev[nxt] = cur
                queue.append(nxt)
    return prev


def reachability(
    index: Index,
    target: str,
    *,
    cve: str | None = None,
    package: str | None = None,
    max_paths: int = 10,
) -> dict[str, Any]:
    """Is ``target`` (a dotted function, method or class name, possibly a
    re-exported one such as ``jwt.decode``) reachable from any HTTP route?

    Returns ``reachable`` = True (resolved call path), "possible" (only through a
    call on a receiver of unknown type with the same method name), "unknown"
    (no path found but some modules failed to parse) or False.
    """
    canonical = index.canonical(target)
    targets = {target, canonical}
    known = any(t in index.functions or t in index.classes for t in targets)

    paths: list[list[str]] = []
    routes_hit: list[str] = []
    seen_routes: set[str] = set()
    for route in sorted(index.routes, key=lambda r: (r.path, r.method)):
        path = _shortest_path(index, route.handler, targets)
        if path is None or route.label in seen_routes:
            continue
        seen_routes.add(route.label)
        routes_hit.append(route.label)
        if len(paths) < max_paths:
            paths.append([route.label, *path])

    possible: list[dict[str, Any]] = []
    if not paths:
        method_name = canonical.rsplit(".", 1)[-1]
        for route in index.routes:
            for func_name, _ in _reachable_functions(index, route.handler).items():
                func = index.functions.get(func_name)
                if func and method_name in func.unresolved_methods:
                    possible.append(
                        {
                            "route": route.label,
                            "function": func_name,
                            "line": func.unresolved_methods[method_name],
                            "call": f".{method_name}(...) on receiver of unknown type",
                        }
                    )
                    break
            if len(possible) >= max_paths:
                break

    reachable: bool | str = True if paths else ("possible" if possible else False)
    # A module we could not parse may contain the missing edge: never claim
    # "not reachable" on incomplete evidence.
    app_errors = [e for e in index.parse_errors]
    if reachable is False and app_errors:
        reachable = "unknown"
    return {
        "cve": cve,
        "package": package,
        "function": target,
        "resolved_function": canonical,
        "function_indexed": known,
        "reachable": reachable,
        "routes": routes_hit,
        "paths": paths,
        "possible_paths": possible,
        "parse_errors": app_errors,
        "stats": {
            "modules": len(index.modules),
            "functions": len(index.functions),
            "routes": len(index.routes),
        },
    }


def list_routes(index: Index) -> list[dict[str, str]]:
    return [
        {"method": r.method, "path": r.path, "handler": r.handler}
        for r in sorted(index.routes, key=lambda r: (r.path, r.method))
    ]


__all__ = ["Route", "list_routes", "reachability"]
