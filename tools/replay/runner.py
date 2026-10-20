"""Replay recorded exchanges against a live deployment and compare behaviour.

Dynamic values are handled so a replay against a *different* database still
lines up with the recording:

* **auth** – every exchange that issued a token is replayed, and later requests
  use the live token from the exchange they referenced (``auth_ref``). Requests
  that used a token from before recording (``auth_ref == 0``) log in first with
  ``login`` credentials.
* **IDs** – IDs in live responses are paired with the recorded ones by position
  and rewritten into later paths, queries and bodies.
* **run-unique values** – values matching ``unique_pattern`` (e.g. signup emails)
  get a per-run suffix so a replay can be repeated against the same database.
* **secrets** – ``${NAME}`` placeholders are filled from ``secrets``.

The comparison is always made in *recorded space*: live values are mapped back
through the inverse value map, then diffed against the reference (the recording,
or a baseline deployment replayed the same way).
"""

from __future__ import annotations

import re
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode

import httpx

from replay.diff import diff
from replay.model import Exchange
from replay.values import ValueMap, endpoint_template, fill_placeholders

DEFAULT_UNIQUE = r"[A-Za-z0-9._%+-]+@replay\.example\.com"


@dataclass
class Login:
    path: str
    username: str
    password: str


@dataclass
class LiveResult:
    seq: int
    status: int
    body: Any
    latency_ms: float
    error: str | None = None


@dataclass
class Run:
    base_url: str
    results: dict[int, LiveResult] = field(default_factory=dict)
    values: ValueMap = field(default_factory=ValueMap)


def _parse(resp: httpx.Response) -> Any:
    if not resp.content:
        return None
    if "json" in resp.headers.get("content-type", ""):
        try:
            return resp.json()
        except ValueError:
            pass
    return resp.text


def replay(
    exchanges: list[Exchange],
    base_url: str,
    *,
    login: Login | None = None,
    secrets: dict[str, str] | None = None,
    unique_pattern: str = DEFAULT_UNIQUE,
    run_id: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> Run:
    secrets = dict(secrets or {})
    run = Run(base_url=base_url)
    run_id = run_id or uuid.uuid4().hex[:8]
    owns_client = client is None
    client = client or httpx.Client(base_url=base_url, timeout=timeout)
    tokens: dict[int, str] = {}

    # run-unique values: pre-seed the value map
    pattern = re.compile(unique_pattern)
    for ex in exchanges:
        for text in (ex.path, ex.query, repr(ex.body)):
            for m in pattern.findall(text):
                local, _, domain = m.partition("@")
                run.values.add(m, f"{local}-{run_id}@{domain}")

    try:
        if login and any(ex.auth_ref == 0 for ex in exchanges):
            body = fill_placeholders({"username": login.username, "password": login.password}, secrets)
            resp = client.post(login.path, data=body)
            resp.raise_for_status()
            tokens[0] = resp.json()["access_token"]

        for ex in exchanges:
            path = run.values.to_live(ex.path)
            query = ex.query
            if query:
                query = urlencode([(k, run.values.to_live(v)) for k, v in parse_qsl(query, keep_blank_values=True)])
            body = fill_placeholders(run.values.to_live(ex.body), secrets)
            headers = {}
            if ex.auth_ref is not None and ex.auth_ref in tokens:
                headers["Authorization"] = f"Bearer {tokens[ex.auth_ref]}"
            kwargs: dict[str, Any] = {"headers": headers}
            if body is not None:
                if ex.content_type == "application/x-www-form-urlencoded" and isinstance(body, dict):
                    kwargs["data"] = body
                elif ex.content_type == "application/json":
                    kwargs["json"] = body
                else:
                    kwargs["content"] = body if isinstance(body, str) else str(body)
            url = path + (f"?{query}" if query else "")
            start = time.perf_counter()
            try:
                resp = client.request(ex.method, url, **kwargs)
            except httpx.HTTPError as exc:
                run.results[ex.seq] = LiveResult(ex.seq, 0, None, 0.0, error=str(exc))
                continue
            latency = (time.perf_counter() - start) * 1000
            live = _parse(resp)
            run.results[ex.seq] = LiveResult(ex.seq, resp.status_code, live, round(latency, 2))
            if isinstance(live, dict) and isinstance(live.get("access_token"), str):
                tokens[ex.seq] = live["access_token"]
            run.values.learn(ex.response, live)
    finally:
        if owns_client:
            client.close()
    return run


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1], 2)


def compare(
    exchanges: list[Exchange],
    candidate: Run,
    baseline: Run | None = None,
) -> dict[str, Any]:
    """Compare a candidate run against the recording (or a baseline run).

    Diffs that survive volatile-field filtering are reported as
    ``unclassified``: deciding real regression vs noise is the Validation
    agent's job, with this report as its evidence.
    """
    endpoints: dict[str, dict[str, Any]] = {}
    status_mismatches = []
    body_diffs = []
    errors = []
    ref_lat: list[float] = []
    cand_lat: list[float] = []

    for ex in exchanges:
        cand = candidate.results.get(ex.seq)
        if cand is None:
            continue
        ep = endpoint_template(ex.method, ex.path)
        stats = endpoints.setdefault(ep, {"requests": 0, "status_mismatches": 0, "body_diffs": 0})
        stats["requests"] += 1
        if cand.error:
            errors.append({"seq": ex.seq, "endpoint": ep, "error": cand.error})
            stats["status_mismatches"] += 1
            continue
        if baseline is not None:
            ref = baseline.results.get(ex.seq)
            if ref is None or ref.error:
                continue
            ref_status, ref_body = ref.status, baseline.values.to_recorded(ref.body)
            ref_lat.append(ref.latency_ms)
        else:
            ref_status, ref_body = ex.status, ex.response
            ref_lat.append(ex.latency_ms)
        cand_lat.append(cand.latency_ms)
        cand_body = candidate.values.to_recorded(cand.body)

        if cand.status != ref_status:
            stats["status_mismatches"] += 1
            status_mismatches.append(
                {"seq": ex.seq, "endpoint": ep, "expected": ref_status, "actual": cand.status}
            )
            continue
        # IDs are identity, not behaviour: pair any IDs still differing (e.g. rows
        # that exist only in each deployment's own data) before comparing.
        pairing = ValueMap()
        pairing.learn(ref_body, cand_body)
        d = diff(ref_body, pairing.to_recorded(cand_body))
        if d:
            stats["body_diffs"] += 1
            body_diffs.append(
                {
                    "seq": ex.seq,
                    "endpoint": ep,
                    "query": ex.query,
                    "diffs": d,
                    "classification": "unclassified",
                }
            )

    p95_before, p95_after = _pct(ref_lat, 95), _pct(cand_lat, 95)
    # Only a baseline run is measured in the same environment as the candidate; the
    # recording's latencies come from wherever it was captured, so they're informational.
    latency_comparable = baseline is not None
    latency_regression = bool(latency_comparable and p95_before and p95_after and p95_after > 1.2 * p95_before)
    return {
        "reference": "baseline" if baseline else "recording",
        "target": candidate.base_url,
        "baseline": baseline.base_url if baseline else None,
        "requests": len(candidate.results),
        "endpoints": len(endpoints),
        "status_mismatches": len(status_mismatches),
        "body_diffs": len(body_diffs),
        "errors": errors,
        "p50_ms_before": _pct(ref_lat, 50),
        "p95_ms_before": p95_before,
        "p50_ms_after": _pct(cand_lat, 50),
        "p95_ms_after": p95_after,
        "latency_comparable": latency_comparable,
        "latency_regression": latency_regression,
        "per_endpoint": endpoints,
        "status_mismatch_details": status_mismatches,
        "body_diff_details": body_diffs,
    }
