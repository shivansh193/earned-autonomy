"""Classify the resource changes in ``terraform show -json plan.out``.

Each change becomes ``{type, resource, risky, reason}`` where ``type`` is
create / update / delete / replace / read / no-op. A change is **risky** when:

* a stateful resource (database, bucket, disk, secret, ...) is deleted or replaced;
* an IAM change grants a role to ``allUsers`` / ``allAuthenticatedUsers`` or grants
  owner/editor-level roles;
* a production Cloud Run service is opened to all ingress, or its minimum
  instances drop to 0 (cold starts in prod);
* deletion protection is turned off on a resource that has it.

The cost delta covers what we can estimate honestly from a plan: always-on Cloud Run
minimum instances (see ``PRICES``). It is an estimate. Request-driven usage is not in
the plan, so it is excluded.
"""

from __future__ import annotations

import re
from typing import Any

STATEFUL = {
    "google_sql_database_instance",
    "google_sql_database",
    "google_storage_bucket",
    "google_firestore_database",
    "google_redis_instance",
    "google_compute_disk",
    "google_secret_manager_secret",
    "google_artifact_registry_repository",
    "google_bigquery_dataset",
}
PUBLIC_MEMBERS = {"allUsers", "allAuthenticatedUsers"}
BROAD_ROLES = {"roles/owner", "roles/editor", "roles/iam.securityAdmin", "roles/resourcemanager.projectIamAdmin"}

# USD, Cloud Run instance-based ("CPU always allocated") list prices, us-central1 tier 1.
# VERIFY against https://cloud.google.com/run/pricing before quoting cost numbers: these
# are the long-standing published rates, but the page could not be loaded on 2026-10-21.
# A min instance is billed for every second of the month. Update alongside the region choice.
PRICES = {"vcpu_second": 0.000018, "gib_second": 0.000002}
SECONDS_PER_MONTH = 30 * 24 * 3600


def _actions(change: dict[str, Any]) -> str:
    acts = change.get("actions", [])
    if acts in (["delete", "create"], ["create", "delete"]):
        return "replace"
    if acts == ["no-op"]:
        return "no-op"
    return acts[0] if acts else "no-op"


def _first(x: Any) -> Any:
    return x[0] if isinstance(x, list) and x else (x if isinstance(x, dict) else {})


def _cpu(value: Any) -> float:
    s = str(value or "1")
    return float(s[:-1]) / 1000 if s.endswith("m") else float(s)


def _gib(value: Any) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(Mi|Gi|M|G)?", str(value or "512Mi"))
    if not m:
        return 0.5
    n, unit = float(m.group(1)), m.group(2) or "Mi"
    return {"Mi": n / 1024, "M": n * 1e6 / 2**30, "Gi": n, "G": n * 1e9 / 2**30}[unit]


def cloud_run_profile(attrs: dict[str, Any] | None) -> dict[str, Any] | None:
    """min instances, cpu, memory and ingress of a google_cloud_run_v2_service state."""
    if not attrs:
        return None
    tmpl = _first(attrs.get("template"))
    scaling = _first(tmpl.get("scaling")) or _first(attrs.get("scaling"))
    container = _first(tmpl.get("containers"))
    limits = (_first(container.get("resources")) or {}).get("limits") or {}
    return {
        "name": attrs.get("name"),
        "min_instances": int(scaling.get("min_instance_count") or 0),
        "cpu": _cpu(limits.get("cpu")),
        "memory_gib": _gib(limits.get("memory")),
        "ingress": attrs.get("ingress"),
    }


def monthly_cost(profile: dict[str, Any] | None) -> float:
    if not profile:
        return 0.0
    per_second = profile["cpu"] * PRICES["vcpu_second"] + profile["memory_gib"] * PRICES["gib_second"]
    return profile["min_instances"] * per_second * SECONDS_PER_MONTH


def _is_prod(rc: dict[str, Any], attrs: dict[str, Any] | None) -> bool:
    name = str((attrs or {}).get("name", "")) + " " + rc.get("address", "")
    return bool(re.search(r"prod", name, re.I))


def _iam_members(attrs: dict[str, Any] | None) -> set[str]:
    if not attrs:
        return set()
    members = set(attrs.get("members") or [])
    if attrs.get("member"):
        members.add(attrs["member"])
    return members


MAX_KEYS = 40  # per resource, to keep the evidence bundle small enough for an agent to read


def _flatten(value: Any, sens: Any, unknown: Any, prefix: str, out: dict[str, Any]) -> None:
    """Flatten nested plan values to dotted paths, masking sensitive and computed values."""
    if sens is True:
        out[prefix] = "(sensitive)"
    elif unknown is True:
        out[prefix] = "(known after apply)"
    elif isinstance(value, dict):
        for k, v in value.items():
            _flatten(v, (sens or {}).get(k) if isinstance(sens, dict) else None,
                     (unknown or {}).get(k) if isinstance(unknown, dict) else None,
                     f"{prefix}.{k}" if prefix else k, out)
    elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        for i, v in enumerate(value):
            _flatten(v, sens[i] if isinstance(sens, list) and i < len(sens) else None,
                     unknown[i] if isinstance(unknown, list) and i < len(unknown) else None, f"{prefix}[{i}]", out)
    else:
        out[prefix] = value


def attribute_diff(rc: dict[str, Any]) -> dict[str, list[Any]]:
    """{path: [before, after]} for every attribute the change modifies (values shortened)."""
    ch = rc.get("change", {})
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    _flatten(ch.get("before") or {}, ch.get("before_sensitive"), None, "", before)
    _flatten(ch.get("after") or {}, ch.get("after_sensitive"), ch.get("after_unknown"), "", after)

    def short(v: Any) -> Any:
        return v[:120] + "..." if isinstance(v, str) and len(v) > 120 else v

    diff = {k: [short(before.get(k)), short(after.get(k))] for k in sorted(set(before) | set(after))
            if before.get(k) != after.get(k) and after.get(k) != "(known after apply)"}
    return dict(list(diff.items())[:MAX_KEYS])


def classify(rc: dict[str, Any]) -> dict[str, Any]:
    change = rc.get("change", {})
    kind = _actions(change)
    rtype = rc.get("type", "")
    before, after = change.get("before"), change.get("after")
    reasons: list[str] = []

    if kind in ("delete", "replace") and rtype in STATEFUL:
        reasons.append(f"{kind}s a stateful resource ({rtype}); data may be lost")

    if "_iam_" in rtype and kind in ("create", "update", "replace"):
        new_public = (_iam_members(after) & PUBLIC_MEMBERS) - (_iam_members(before) & PUBLIC_MEMBERS)
        if new_public:
            reasons.append(f"grants {(after or {}).get('role')} to {', '.join(sorted(new_public))} (public access)")
        if (after or {}).get("role") in BROAD_ROLES:
            reasons.append(f"grants broad role {(after or {}).get('role')}")

    if rtype == "google_cloud_run_v2_service" and kind in ("update", "replace", "create") and _is_prod(rc, after):
        b, a = cloud_run_profile(before), cloud_run_profile(after)
        if a and a["min_instances"] == 0 and (b is None or b["min_instances"] > 0):
            reasons.append("production min instances set to 0 (cold starts)")
        if a and a["ingress"] == "INGRESS_TRAFFIC_ALL" and b and b["ingress"] != "INGRESS_TRAFFIC_ALL":
            reasons.append("production ingress opened to all traffic")

    if (before or {}).get("deletion_protection") is True and (after or {}).get("deletion_protection") is False:
        reasons.append("turns off deletion protection")

    return {
        "type": kind,
        "resource": rc.get("address"),
        "resource_type": rtype,
        "risky": bool(reasons),
        "reason": "; ".join(reasons) or None,
    }


def analyse(plan: dict[str, Any]) -> dict[str, Any]:
    changes = [classify(rc) for rc in plan.get("resource_changes", []) if rc.get("mode", "managed") == "managed"]
    cost = 0.0
    for rc in plan.get("resource_changes", []):
        if rc.get("type") == "google_cloud_run_v2_service":
            ch = rc.get("change", {})
            cost += monthly_cost(cloud_run_profile(ch.get("after"))) - monthly_cost(cloud_run_profile(ch.get("before")))
    summary: dict[str, int] = {}
    for c in changes:
        summary[c["type"]] = summary.get(c["type"], 0) + 1
    return {
        "terraform_version": plan.get("terraform_version"),
        "changes": [c for c in changes if c["type"] != "no-op"],
        # What exactly changes, for the Infra agent (rules above only know fixed patterns).
        "attribute_changes": [
            {"resource": rc.get("address"), "type": _actions(rc.get("change", {})), "changed": attribute_diff(rc)}
            for rc in plan.get("resource_changes", [])
            if rc.get("mode", "managed") == "managed" and _actions(rc.get("change", {})) != "no-op"
        ],
        "summary": summary,
        "cost_delta_usd_month": round(cost, 2),
        "cost_basis": "always-on Cloud Run min instances at PRICES in tools/tfplan/parse.py; estimate",
    }
