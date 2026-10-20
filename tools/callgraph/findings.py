"""Turn a dependency audit into reachability findings.

Input: ``pip-audit -f json`` output (OSV/PyPI advisories for the locked deps).
Advisories rarely name the vulnerable *function*, so we keep a small curated map,
``advisories.json``: ``{advisory id or alias: {"function": "pkg.mod.func", "severity": "high"}}``.
A finding with no mapped function gets ``function: null``; the risk engine
treats it as reachable with no runtime data (conservative) rather than ignoring it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def from_pip_audit(audit: dict[str, Any], advisories: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for dep in audit.get("dependencies", []):
        for v in dep.get("vulns", []):
            ids = [v["id"], *v.get("aliases", [])]
            mapped = next((advisories[i] for i in ids if i in advisories), {})
            cve = next((i for i in ids if i.startswith("CVE-")), v["id"])
            out.append(
                {
                    "cve": cve,
                    "advisory": v["id"],
                    "package": dep["name"],
                    "version": dep.get("version"),
                    "fix_versions": v.get("fix_versions", []),
                    "function": mapped.get("function"),
                    "severity": mapped.get("severity", "unknown"),
                }
            )
    return out


def load_advisories(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: {f: x for f, x in v.items() if not f.startswith("_")}
            for k, v in data.items() if not k.startswith("_")}
