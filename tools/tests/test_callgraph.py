import json
from pathlib import Path

import pytest

from callgraph import build_index, reachability
from callgraph.cli import main
from callgraph.reach import list_routes

FIX = Path(__file__).parent / "fixtures" / "cg"
APP = FIX / "app"
SITE = FIX / "site"


@pytest.fixture(scope="module")
def index():
    return build_index(APP, ["shop"], [SITE], ["fakejwt", "yamlish"])


def test_routes_full_paths(index):
    routes = {(r["method"], r["path"]) for r in list_routes(index)}
    assert routes == {
        ("GET", "/api/v1/items/"),
        ("POST", "/api/v1/items/import"),
        ("GET", "/api/v1/health"),
    }


def test_reexport_and_module_alias_resolve_to_method(index):
    assert index.canonical("fakejwt.decode") == "fakejwt.api_jwt.PyJWT.decode"


def test_reachable_through_annotated_dependency(index):
    res = reachability(index, "fakejwt.decode", cve="CVE-TEST-1", package="fakejwt")
    assert res["reachable"] is True
    assert res["routes"] == ["GET /api/v1/items/"]
    assert res["paths"][0] == [
        "GET /api/v1/items/",
        "shop.api.routes.items.list_items",
        "shop.deps.get_current_user",
        "shop.security.read_token",
        "fakejwt.api_jwt.PyJWT.decode",
    ]


def test_transitive_into_dependency_internals(index):
    res = reachability(index, "fakejwt.algorithms.verify_signature")
    assert res["reachable"] is True
    assert res["paths"][0][-2:] == ["fakejwt.api_jwt.PyJWT.decode", "fakejwt.algorithms.verify_signature"]


def test_inherited_method_via_local_instance(index):
    res = reachability(index, "shop.helpers.Base.clean")
    assert res["reachable"] is True
    assert "shop.helpers.Base.render" in res["paths"][0]


def test_function_only_used_off_route_is_not_reachable(index):
    # yamlish.construct is called only via yamlish.load, which only an unrouted helper calls
    res = reachability(index, "yamlish.construct")
    assert res["function_indexed"] is True
    assert res["reachable"] is False
    assert res["possible_paths"] == []


def test_unknown_receiver_reported_as_possible(index):
    # import_items calls loader.load(...) on an untyped parameter: we can't prove the
    # call reaches yamlish.load, but we must not hide it either.
    res = reachability(index, "yamlish.load")
    assert res["reachable"] == "possible"
    assert res["possible_paths"][0]["route"] == "POST /api/v1/items/import"
    assert res["possible_paths"][0]["function"] == "shop.api.routes.items.import_items"


def test_unused_function_not_reachable(index):
    assert reachability(index, "fakejwt.encode")["reachable"] is False


def test_cli_batch(tmp_path, capsys):
    findings = tmp_path / "f.json"
    findings.write_text(
        json.dumps(
            [
                {"cve": "CVE-1", "package": "fakejwt", "function": "fakejwt.decode", "severity": "high"},
                {"cve": "CVE-2", "package": "yamlish", "function": "yamlish.construct", "severity": "medium"},
                {"cve": "CVE-3", "package": "other", "function": None, "severity": "low"},
            ]
        )
    )
    rc = main(
        [
            "batch",
            "--app-root", str(APP),
            "--app-package", "shop",
            "--site-packages", str(SITE),
            "--findings", str(findings),
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [o["reachable"] for o in out] == [True, False, None]
    assert out[0]["severity"] == "high"


def test_unparseable_module_makes_negative_answer_unknown(tmp_path):
    pkg = tmp_path / "bad"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "broken.py").write_text("def f(:\n    pass\n")
    idx = build_index(tmp_path, ["bad"])
    res = reachability(idx, "somepkg.vuln")
    assert res["reachable"] == "unknown"
    assert res["parse_errors"][0]["module"] == "bad.broken"


def test_findings_map_through_aliases_and_keep_unmapped():
    from callgraph.findings import from_pip_audit, load_advisories

    adv = load_advisories(Path(__file__).parents[1] / "callgraph" / "advisories.json")
    audit = {"dependencies": [{"name": "jinja2", "version": "3.1.4", "vulns": [
        {"id": "PYSEC-2026-1475", "aliases": ["CVE-2024-56326", "GHSA-q2x7-8rv6-6q7h"], "fix_versions": ["3.1.5"]},
        {"id": "GHSA-zzzz-unmapped", "aliases": [], "fix_versions": []}]}]}
    out = from_pip_audit(audit, adv)
    assert out[0]["cve"] == "CVE-2024-56326"
    assert out[0]["function"] == "jinja2.sandbox.SandboxedEnvironment.call"
    assert "_evidence" not in adv["GHSA-q2x7-8rv6-6q7h"]
    assert out[1]["function"] is None and out[1]["cve"] == "GHSA-zzzz-unmapped"


def test_function_references_count_as_calls(tmp_path):
    """A route that picks a function from a dispatch table, or passes one to
    asyncio.to_thread, still reaches it (seen on VaultScope's report endpoint)."""
    pkg = tmp_path / "disp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "render.py").write_text(
        "def pdf(x):\n    return sink(x)\n\n"
        "def html(x):\n    return x\n\n"
        "def sink(x):\n    return x\n"
    )
    (pkg / "main.py").write_text(
        "import asyncio\n"
        "from fastapi import FastAPI\n"
        "from disp import render\n\n"
        "app = FastAPI()\n"
        "BUILDERS = {'pdf': ('pdf', render.pdf), 'html': ('html', lambda x: render.html(x))}\n\n"
        "@app.post('/report/{kind}')\n"
        "async def report(kind: str):\n"
        "    suffix, builder = BUILDERS[kind]\n"
        "    return await asyncio.to_thread(builder, kind)\n\n"
        "@app.get('/other')\n"
        "def other():\n"
        "    return 1\n"
    )
    idx = build_index(tmp_path, ["disp"])
    for fn in ("disp.render.pdf", "disp.render.sink", "disp.render.html"):
        res = reachability(idx, fn)
        assert res["reachable"] is True, fn
        assert res["routes"] == ["POST /report/{kind}"], fn
