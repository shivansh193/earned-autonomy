"""ea-callgraph: static reachability of (vulnerable) functions from HTTP routes.

Examples
--------
List routes::

    ea-callgraph routes --app-root backend --app-package app

One query::

    ea-callgraph reach --app-root backend --app-package app \\
        --site-packages backend/.venv/lib/python3.14/site-packages --dep-package jwt \\
        --function jwt.decode --cve CVE-XXXX-YYYY --package pyjwt

Batch (one JSON object per finding in, one result per finding out)::

    ea-callgraph batch --findings findings.json ... > reachability.json

``findings.json`` is a list of ``{"cve", "package", "function", "severity"}`` where
``function`` is the vulnerable callable (we keep a small curated map from advisory
to function in ``tools/callgraph/advisories.json``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from callgraph.index import build_index
from callgraph.reach import list_routes, reachability


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--app-root", type=Path, required=True)
    p.add_argument("--app-package", action="append", required=True)
    p.add_argument("--site-packages", type=Path, action="append", default=[])
    p.add_argument("--dep-package", action="append", default=[])
    p.add_argument(
        "--define",
        action="append",
        default=[],
        help="Override a constant used in route prefixes, e.g. settings.API_V1_STR=/api/v1",
    )


def _index(args: argparse.Namespace, extra_deps: list[str] | None = None):
    defines = dict(d.split("=", 1) for d in args.define)
    deps = list(dict.fromkeys([*args.dep_package, *(extra_deps or [])]))
    return build_index(args.app_root, args.app_package, args.site_packages, deps, defines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ea-callgraph", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_routes = sub.add_parser("routes", help="list HTTP routes found")
    _common(p_routes)

    p_reach = sub.add_parser("reach", help="is a function reachable from a route?")
    _common(p_reach)
    p_reach.add_argument("--function", required=True)
    p_reach.add_argument("--cve")
    p_reach.add_argument("--package")

    p_batch = sub.add_parser("batch", help="reachability for a list of findings")
    _common(p_batch)
    p_batch.add_argument("--findings", type=Path, required=True)

    p_find = sub.add_parser("findings", help="pip-audit JSON -> findings for `batch`")
    p_find.add_argument("--pip-audit", type=Path, required=True)
    p_find.add_argument("--advisories", type=Path, default=Path(__file__).with_name("advisories.json"))

    args = parser.parse_args(argv)
    if args.cmd == "findings":
        from callgraph.findings import from_pip_audit, load_advisories

        audit = json.loads(args.pip_audit.read_text(encoding="utf-8"))
        json.dump(from_pip_audit(audit, load_advisories(args.advisories)), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.cmd == "routes":
        out: object = list_routes(_index(args))
    elif args.cmd == "reach":
        out = reachability(_index(args), args.function, cve=args.cve, package=args.package)
    else:
        findings = json.loads(args.findings.read_text(encoding="utf-8"))
        top_levels = [f["function"].split(".")[0] for f in findings if f.get("function")]
        index = _index(args, top_levels)
        out = []
        for f in findings:
            if not f.get("function"):
                out.append({**f, "reachable": None, "reason": "no known vulnerable function"})
                continue
            res = reachability(index, f["function"], cve=f.get("cve"), package=f.get("package"))
            out.append({**res, "severity": f.get("severity")})
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
