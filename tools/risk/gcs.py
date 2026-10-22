"""Keep the trust ledger in Google Cloud Storage so every pipeline shares one history.

Authentication is keyless: a GitLab CI job's OIDC token (``id_tokens:
GITLAB_OIDC_TOKEN``) is exchanged for a short-lived deployer token through
Workload Identity Federation (same flow as ci/gcp-token.sh, in Python so jobs
don't need jq). Locally, ``GCP_ACCESS_TOKEN`` can be set instead
(``gcloud auth print-access-token``).

Concurrent writers are safe: uploads use ``ifGenerationMatch``, so a push based on
a stale copy fails instead of overwriting someone else's entries.

    ea-ledger pull  --bucket B --object trust/ledger.jsonl --out .trust/ledger.jsonl
    ea-ledger append --bucket B --object trust/ledger.jsonl --entries new.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

STS = "https://sts.googleapis.com/v1/token"
IAM = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa}:generateAccessToken"
GCS = "https://storage.googleapis.com"


def _req(url: str, data: bytes | None = None, headers: dict[str, str] | None = None,
         method: str | None = None) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def access_token() -> str:
    if os.environ.get("GCP_ACCESS_TOKEN"):
        return os.environ["GCP_ACCESS_TOKEN"]
    oidc = os.environ["GITLAB_OIDC_TOKEN"]
    aud = (f"//iam.googleapis.com/projects/{os.environ['GCP_PROJECT_NUMBER']}"
           "/locations/global/workloadIdentityPools/gitlab-pool/providers/gitlab")
    body = json.dumps({
        "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
        "audience": aud,
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
        "subjectToken": oidc,
        "subjectTokenType": "urn:ietf:params:oauth:token-type:jwt",
    }).encode()
    st, raw, _ = _req(STS, body, {"Content-Type": "application/json"})
    if st != 200:
        raise RuntimeError(f"STS exchange failed: {st}")
    fed = json.loads(raw)["access_token"]
    st, raw, _ = _req(IAM.format(sa=os.environ["GCP_DEPLOYER_SA"]),
                      json.dumps({"scope": ["https://www.googleapis.com/auth/cloud-platform"],
                                  "lifetime": "1800s"}).encode(),
                      {"Content-Type": "application/json", "Authorization": f"Bearer {fed}"})
    if st != 200:
        raise RuntimeError(f"generateAccessToken failed: {st}")
    token = json.loads(raw)["accessToken"]
    os.environ["GCP_ACCESS_TOKEN"] = token
    return token


def download(bucket: str, obj: str, token: str) -> tuple[bytes, int]:
    """Object bytes and generation; (b"", 0) if it doesn't exist yet."""
    url = f"{GCS}/storage/v1/b/{bucket}/o/{urllib.parse.quote(obj, safe='')}?alt=media"
    st, raw, headers = _req(url, headers={"Authorization": f"Bearer {token}"})
    if st == 404:
        return b"", 0
    if st != 200:
        raise RuntimeError(f"download failed: {st} {raw[:200]!r}")
    gen = headers.get("x-goog-generation") or headers.get("X-Goog-Generation") or "0"
    return raw, int(gen)


def upload(bucket: str, obj: str, data: bytes, generation: int, token: str) -> bool:
    """Upload only if the object is still at ``generation`` (0 = must not exist)."""
    q = urllib.parse.urlencode({"uploadType": "media", "name": obj, "ifGenerationMatch": generation})
    st, raw, _ = _req(f"{GCS}/upload/storage/v1/b/{bucket}/o?{q}", data,
                      {"Authorization": f"Bearer {token}", "Content-Type": "application/x-ndjson"}, "POST")
    if st == 412:
        return False
    if st not in (200, 201):
        raise RuntimeError(f"upload failed: {st} {raw[:200]!r}")
    return True


def append(bucket: str, obj: str, entries: list[dict[str, Any]], token: str, attempts: int = 6) -> int:
    """Append entries atomically (read, append, conditional write, retry on races)."""
    lines = "".join(json.dumps(e, sort_keys=True) + "\n" for e in entries).encode()
    for i in range(attempts):
        current, gen = download(bucket, obj, token)
        if current and not current.endswith(b"\n"):
            current += b"\n"
        if upload(bucket, obj, current + lines, gen, token):
            return len(entries)
        time.sleep(0.5 * (i + 1))
    raise RuntimeError("ledger append lost the race too many times")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ea-ledger")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("pull", "append"):
        s = sub.add_parser(name)
        s.add_argument("--bucket", default=os.environ.get("EA_LEDGER_BUCKET"), required=not os.environ.get("EA_LEDGER_BUCKET"))
        s.add_argument("--object", default="trust/ledger.jsonl")
        if name == "pull":
            s.add_argument("--out", type=Path, required=True)
        else:
            s.add_argument("--entries", type=Path, required=True)
    r = sub.add_parser("report", help="render the trust ledger as one HTML page")
    r.add_argument("--ledger", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--notes", type=Path, help="JSON {mr_iid: note} shown beside a release")
    r.add_argument("--source", default="")
    a = p.parse_args(argv)
    if a.cmd == "report":
        from datetime import UTC, datetime

        from risk.report import main_report
        main_report(a.ledger, a.out, a.source, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), a.notes)
        sys.stdout.write(f"report: {a.out}\n")
        return 0
    token = access_token()
    if a.cmd == "pull":
        data, gen = download(a.bucket, a.object, token)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_bytes(data)
        sys.stdout.write(f"ledger: {len(data.splitlines())} entries (generation {gen})\n")
    else:
        entries = [json.loads(l) for l in a.entries.read_text(encoding="utf-8").splitlines() if l.strip()]
        n = append(a.bucket, a.object, entries, token)
        sys.stdout.write(f"ledger: appended {n} entries\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
