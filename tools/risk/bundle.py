"""Assemble one evidence bundle per pipeline from the collectors' outputs.

Every input is optional: a missing collector shows up as ``null`` in the bundle
and the risk engine treats missing evidence conservatively (see engine.py).
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from callgraph.index import Index


def junit_summary(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = failures = errors = skipped = 0
    for s in suites:
        total += int(s.get("tests", 0))
        failures += int(s.get("failures", 0))
        errors += int(s.get("errors", 0))
        skipped += int(s.get("skipped", 0))
    failed = failures + errors
    return {"passed": total - failed - skipped, "failed": failed, "skipped": skipped}


def parse_diff(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Unified diff (``git diff -U0``) -> {new-file path: [(first, last) changed lines]}.

    Pure deletions are recorded as the single line they sit after, so a removed
    line inside a function still marks that function as changed.
    """
    out: dict[str, list[tuple[int, int]]] = {}
    path = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            path = None if target == "/dev/null" else target.removeprefix("b/")
            if path:
                out.setdefault(path, [])
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                out[path].append((start, start + max(count, 1) - 1))
    return out


def changed_functions(index: Index, diff: dict[str, list[tuple[int, int]]], repo_root: Path) -> tuple[set[str], set[Path]]:
    """Functions whose source lines a diff touches, plus files where a hunk hit
    module-level code (those fall back to file-level scope)."""
    by_file: dict[Path, list[tuple[int, int, str]]] = {}
    for q, f in index.functions.items():
        node = f.node
        first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        by_file.setdefault(index.modules[f.module].path.resolve(), []).append((first, node.end_lineno or first, q))
    funcs: set[str] = set()
    whole: set[Path] = set()
    for rel, hunks in diff.items():
        p = (repo_root / rel).resolve()
        spans = by_file.get(p, [])
        for a, b in hunks:
            inside = [q for lo, hi, q in spans if lo <= b and a <= hi]
            if inside:
                funcs.update(inside)
            elif spans:
                whole.add(p)
    return funcs, whole


def routes_touched(
    index: Index,
    changed_files: list[str],
    repo_root: Path,
    diff: dict[str, list[tuple[int, int]]] | None = None,
) -> list[str]:
    """Routes whose handler, or any function it can reach, changed.

    With a diff, scope is function-level (only the functions whose lines changed);
    without one it is file-level (any function in a changed file).
    """
    func_file = {q: index.modules[f.module].path.resolve() for q, f in index.functions.items()}
    if diff is not None:
        funcs, whole_files = changed_functions(index, diff, repo_root)

        def is_changed(q: str) -> bool:
            return q in funcs or func_file.get(q) in whole_files
    else:
        files = {(repo_root / f).resolve() for f in changed_files}

        def is_changed(q: str) -> bool:
            return func_file.get(q) in files

    hit: set[str] = set()
    for route in index.routes:
        seen = {route.handler}
        stack = [route.handler]
        while stack:
            cur = stack.pop()
            if is_changed(cur):
                hit.add(route.label)
                break
            fn = index.functions.get(cur)
            for nxt in fn.calls if fn else ():
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
    return sorted(hit)


def security_findings(
    reachability: list[dict[str, Any]] | None,
    hits: dict[str, Any] | None,
    baseline: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    """Join reachability with runtime hits. With ``baseline`` (the target branch's
    findings), each finding is marked ``new``; without it ``new`` is None and the
    engine treats it as new (conservative)."""
    if reachability is None:
        return None
    counts = (hits or {}).get("hits", {}) if hits else None
    known = {(b.get("cve"), b.get("package")) for b in baseline} if baseline is not None else None
    out = []
    for r in reachability:
        fn = r.get("function")
        runtime = None
        if counts is not None and fn is not None:
            runtime = counts.get(fn, counts.get(r.get("resolved_function", ""), None))
        out.append(
            {
                "cve": r.get("cve"),
                "package": r.get("package"),
                "function": fn,
                "severity": (r.get("severity") or "unknown").lower(),
                "static_reachable": r.get("reachable"),
                "paths": r.get("paths", [])[:3],
                "possible_paths": r.get("possible_paths", [])[:3],
                "runtime_hits": runtime,
                "new": None if known is None else (r.get("cve"), r.get("package")) not in known,
            }
        )
    return out


def replay_summary(report: dict[str, Any] | None, verdicts: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Combine the replay report with the Validation agent's per-diff verdicts.

    A diff without a verdict counts as real (conservative).
    """
    if report is None:
        return None
    by_seq = {v["seq"]: v for v in (verdicts or []) if "seq" in v}
    real = noise = 0
    diffs = []
    for d in report.get("body_diff_details", []):
        v = by_seq.get(d["seq"], {})
        verdict = v.get("verdict", "unclassified")
        if verdict == "noise":
            noise += 1
        else:
            real += 1
        diffs.append({"seq": d["seq"], "endpoint": d["endpoint"], "verdict": verdict,
                      "reason": v.get("reason"), "diffs": d["diffs"][:3]})
    return {
        "reference": report.get("reference"),
        "requests": report.get("requests"),
        "endpoints": report.get("endpoints"),
        "status_mismatches": report.get("status_mismatches", 0),
        "status_mismatch_details": report.get("status_mismatch_details", [])[:10],
        "body_diffs_real": real,
        "body_diffs_noise": noise,
        "body_diffs": diffs[:20],
        "p95_ms_before": report.get("p95_ms_before"),
        "p95_ms_after": report.get("p95_ms_after"),
        "latency_comparable": report.get("latency_comparable", False),
        "latency_regression": report.get("latency_regression", False),
    }


def build_bundle(
    *,
    mr_iid: int | None,
    commit: str | None,
    pipeline_id: int | None,
    changed_files: list[str],
    routes: list[str],
    tests: dict[str, int] | None,
    security: list[dict[str, Any]] | None,
    replay: dict[str, Any] | None,
    infra: dict[str, Any] | None,
    planning_prior: float | None = None,
) -> dict[str, Any]:
    infra_touched = any(f.startswith("infra/") for f in changed_files)
    areas = [f"route:{r}" for r in routes]
    if infra:
        areas += [f"infra:{c['resource']}" for c in infra.get("changes", [])]
    if not areas:
        tops = sorted({f.split("/", 1)[0] for f in changed_files})
        areas = [f"files:{t}" for t in tops] or ["files:none"]
    return {
        "schema": "earned-autonomy/bundle/v1",
        "mr_iid": mr_iid,
        "commit": commit,
        "change_summary": {
            "files": len(changed_files),
            "changed_files": changed_files[:200],
            "routes_touched": routes,
            "infra_touched": infra_touched,
        },
        "areas": areas,
        "tests": tests,
        "security": security,
        "replay": replay,
        "infra": infra,
        "planning_prior": planning_prior,
        "risk": None,
        "autonomy": None,
        "provenance": {
            "pipeline_id": pipeline_id,
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    }


def load_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
