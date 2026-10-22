"""Precompute dependency fixes as small, verifiable text edits.

For each vulnerable package with a published fix version, run
``uv lock --upgrade-package <pkg>==<fix>`` on a scratch copy of the workspace and
reduce the result to ``old_str -> new_str`` edits of the changed lock blocks (and
of pyproject.toml lines, if a constraint had to change). An edit set is kept only
if applying it to the original files reproduces uv's output exactly.

The Triage agent turns these into a fix MR with the GitLab ``create_commit`` tool
(server-side old/new replacement), so no model ever writes a lock file.

    python -m callgraph.fixes --findings reports/head-findings.json --out reports/fixes.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

FILES = ["pyproject.toml", "backend/pyproject.toml", "uv.lock"]


def _vkey(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"[.\-+]", v))


def lock_blocks(text: str) -> dict[str, str]:
    """``[[package]]`` blocks of a uv.lock keyed by ``name@version``."""
    blocks = {}
    for chunk in text.split("\n[[package]]\n")[1:]:
        block = "[[package]]\n" + chunk.rstrip("\n").split("\n\n[")[0]
        name = re.search(r'^name = "([^"]+)"', block, re.M)
        ver = re.search(r'^version = "([^"]+)"', block, re.M)
        if name:
            blocks[f"{name.group(1)}@{ver.group(1) if ver else ''}"] = block
    return blocks


def edits_for(old: str, new: str, package: str) -> list[tuple[str, str]]:
    """old -> new block pairs for the package's lock entries."""
    ob = {k: v for k, v in lock_blocks(old).items() if k.split("@")[0] == package}
    nb = {k: v for k, v in lock_blocks(new).items() if k.split("@")[0] == package}
    if len(ob) != 1 or len(nb) != 1:
        return []
    (o,), (n,) = ob.values(), nb.values()
    return [] if o == n else [(o, n)]


def compute(findings: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    by_pkg: dict[str, dict[str, Any]] = {}
    for f in findings:
        fixes = [v for v in f.get("fix_versions") or [] if f.get("version") and _vkey(v) > _vkey(f["version"])]
        if not fixes:
            continue
        entry = by_pkg.setdefault(f["package"], {"package": f["package"], "from": f["version"], "fix_candidates": set(), "advisories": []})
        entry["fix_candidates"].update(fixes)
        entry["advisories"].append(f.get("cve") or f.get("advisory"))

    out = []
    for pkg, e in by_pkg.items():
        target = max(e["fix_candidates"], key=_vkey)  # one bump that fixes every advisory for the package
        result = {"package": pkg, "from": e["from"], "to": target, "advisories": sorted(set(e["advisories"]))}
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for rel in FILES:
                (t / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, t / rel)
            proc = subprocess.run(["uv", "lock", "--upgrade-package", f"{pkg}=={target}", "-q"],
                                  cwd=t, capture_output=True, text=True)
            if proc.returncode != 0:
                result["error"] = "uv could not resolve the fix: " + (proc.stderr.strip().splitlines() or ["?"])[-1][:300]
                out.append(result)
                continue
            actions = []
            for rel in FILES:
                before = (root / rel).read_text(encoding="utf-8")
                after = (t / rel).read_text(encoding="utf-8")
                if before == after:
                    continue
                pairs = edits_for(before, after, pkg) if rel == "uv.lock" else [(before, after)]
                applied = before
                for o, n in pairs:
                    applied = applied.replace(o, n, 1)
                if not pairs or applied != after:
                    result["error"] = f"change to {rel} is wider than the {pkg} entry; needs a manual fix"
                    actions = []
                    break
                actions += [{"action": "update", "file_path": rel, "old_str": o, "new_str": n} for o, n in pairs]
            if actions:
                result["actions"] = actions
            elif "error" not in result:
                result["error"] = "uv produced no change"
        out.append(result)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m callgraph.fixes")
    p.add_argument("--findings", type=Path, required=True)
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)
    fixes = compute(json.loads(a.findings.read_text(encoding="utf-8")), a.root)
    a.out.write_text(json.dumps(fixes, indent=2) + "\n", encoding="utf-8")
    for f in fixes:
        status = f"{len(f.get('actions', []))} edit(s)" if f.get("actions") else f.get("error")
        sys.stdout.write(f"fix {f['package']} {f['from']} -> {f['to']}: {status}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
