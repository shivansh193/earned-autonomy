"""Minimal Cloud Run Admin API v2 client: revisions, tags, traffic. Stdlib only."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from risk.gcs import _req

RUN = "https://run.googleapis.com/v2"
SECRETS = "https://secretmanager.googleapis.com/v1"
OUTPUT_ONLY = {
    "uid", "generation", "createTime", "updateTime", "deleteTime", "expireTime", "creator",
    "lastModifier", "observedGeneration", "terminalCondition", "conditions", "latestReadyRevision",
    "latestCreatedRevision", "trafficStatuses", "uri", "urls", "reconciling", "etag", "satisfiesPzs",
}


class CloudRun:
    def __init__(self, project: str, region: str, token: str) -> None:
        self.base = f"{RUN}/projects/{project}/locations/{region}"
        self.project = project
        self.h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _json(self, url: str, body: Any = None, method: str | None = None) -> dict[str, Any]:
        st, raw, _ = _req(url, json.dumps(body).encode() if body is not None else None, self.h, method)
        if st >= 300:
            raise RuntimeError(f"{method or 'GET'} {url} -> {st}: {raw[:300]!r}")
        return json.loads(raw or b"{}")

    def service(self, name: str) -> dict[str, Any]:
        return self._json(f"{self.base}/services/{name}")

    def revisions(self, name: str) -> list[dict[str, Any]]:
        """Ready revisions of a service, newest first."""
        revs = self._json(f"{self.base}/services/{name}/revisions?pageSize=50").get("revisions", [])
        ready = [r for r in revs if not r.get("reconciling") and not r.get("deleteTime")]
        return sorted(ready, key=lambda r: r["createTime"], reverse=True)

    def set_traffic(self, name: str, traffic: list[dict[str, Any]], timeout: float = 180) -> dict[str, Any]:
        """Replace the service's traffic split (tags included) and wait for it to apply."""
        svc = self.service(name)
        body = {k: v for k, v in svc.items() if k not in OUTPUT_ONLY}
        body["traffic"] = traffic
        op = self._json(f"{RUN}/{svc['name']}", body, "PATCH")
        deadline = time.time() + timeout
        while not op.get("done") and time.time() < deadline:
            time.sleep(2)
            op = self._json(f"{RUN}/{op['name']}")
        if not op.get("done"):
            raise RuntimeError("traffic update did not finish in time")
        if op.get("error"):
            raise RuntimeError(f"traffic update failed: {op['error']}")
        return self.service(name)

    def secret(self, secret: str) -> str:
        url = f"{SECRETS}/projects/{self.project}/secrets/{secret}/versions/latest:access"
        return base64.b64decode(self._json(url)["payload"]["data"]).decode()


def short(revision: str) -> str:
    return revision.rsplit("/", 1)[-1]


def tag_urls(service: dict[str, Any]) -> dict[str, str]:
    return {t["tag"]: t["uri"] for t in service.get("trafficStatuses", []) if t.get("tag") and t.get("uri")}


def latest_with_tags(previous: str, candidate: str) -> list[dict[str, Any]]:
    """All traffic stays on the latest revision; both get a private tagged URL."""
    return [
        {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100},
        {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": short(previous), "percent": 0, "tag": "previous"},
        {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": short(candidate), "percent": 0, "tag": "candidate"},
    ]


def pinned_to(previous: str, candidate: str) -> list[dict[str, Any]]:
    """Rollback: 100% of traffic to the previous revision, candidate kept tagged for debugging."""
    return [
        {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": short(previous), "percent": 100, "tag": "previous"},
        {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": short(candidate), "percent": 0, "tag": "candidate"},
    ]
