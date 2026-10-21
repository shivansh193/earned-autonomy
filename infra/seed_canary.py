"""Seed the canary account the post-deploy monitor logs in as.

Production carries production-sized data and staging doesn't: the canary owns
150 items in prod but only 5 in staging. That is the honest reason a change can
look fine in CI and staging and still misbehave in prod, and the reason the prod
monitor exists.

Uses only the app's public API (signup, login, create item), so it works against
any deployment. Idempotent: tops the item count up to the target.

    EA_CANARY_PASSWORD=... python infra/seed_canary.py https://api-prod-... 150
"""

from __future__ import annotations

import os
import sys

import httpx

EMAIL = "canary@example.com"


def main() -> int:
    base, target = sys.argv[1].rstrip("/"), int(sys.argv[2])
    pw = os.environ["EA_CANARY_PASSWORD"]
    api = f"{base}/api/v1"
    with httpx.Client(timeout=120) as c:
        r = c.post(f"{api}/users/signup", json={"email": EMAIL, "password": pw, "full_name": "Canary"})
        print("signup:", r.status_code, "(400 = already exists)" if r.status_code == 400 else "")
        tok = c.post(f"{api}/login/access-token", data={"username": EMAIL, "password": pw})
        tok.raise_for_status()
        h = {"Authorization": f"Bearer {tok.json()['access_token']}"}
        have = c.get(f"{api}/items/", params={"limit": 1}, headers=h).json()["count"]
        for i in range(have, target):
            c.post(f"{api}/items/", json={"title": f"Canary item {i + 1:03d}",
                                          "description": "seeded for the post-deploy monitor"},
                   headers=h).raise_for_status()
        print(f"{base}: canary owns {c.get(f'{api}/items/', params={'limit': 1}, headers=h).json()['count']} items (target {target})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
