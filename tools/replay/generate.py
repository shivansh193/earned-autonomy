"""Drive realistic usage of the target API so the recorder has traffic to capture.

The app has no real users yet, so this script plays the parts of an admin and a
few regular users through the real HTTP API. It runs against a server started
with ``EA_RECORD_FILE`` set; what lands in the traffic file is what the server
actually received and answered. Seeded, so runs are reproducible.

    python -m replay.generate --base-url http://localhost:8010 --users 6 --seed 7

Admin credentials come from ``EA_USERNAME`` / ``EA_PASSWORD``. All user emails use
``@replay.example.com`` so the replayer can make them run-unique. Endpoints that
send email are not exercised.
"""

from __future__ import annotations

import argparse
import os
import random
import uuid
from dataclasses import dataclass

import httpx

API = "/api/v1"
WORDS = (
    "invoice receipt shipment ticket backlog roadmap sprint release audit budget "
    "contract draft report review summary plan design proposal spec checklist"
).split()


@dataclass
class Actor:
    email: str
    password: str
    token: str = ""

    @property
    def h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _login(c: httpx.Client, a: Actor) -> None:
    r = c.post(f"{API}/login/access-token", data={"username": a.email, "password": a.password})
    r.raise_for_status()
    a.token = r.json()["access_token"]


def _title(rng: random.Random) -> str:
    return " ".join(rng.sample(WORDS, 2)).capitalize() + f" #{rng.randint(1, 999)}"


def run(base_url: str, users: int, seed: int, admin_email: str, admin_password: str) -> dict[str, int]:
    rng = random.Random(seed)
    tag = uuid.uuid4().hex[:6]  # distinct emails per generation run
    counts = {"requests": 0}

    def hook(_: httpx.Response) -> None:
        counts["requests"] += 1

    with httpx.Client(base_url=base_url, timeout=30, event_hooks={"response": [hook]}) as c:
        admin = Actor(admin_email, admin_password)
        _login(c, admin)
        c.post(f"{API}/login/test-token", headers=admin.h)
        c.get(f"{API}/utils/health-check/")

        actors: list[Actor] = []
        item_ids: dict[str, list[str]] = {}
        for i in range(users):
            a = Actor(f"user{i}-{tag}@replay.example.com", f"pw-{tag}-{i}-{rng.randint(1000, 9999)}")
            r = c.post(f"{API}/users/signup", json={"email": a.email, "password": a.password,
                                                   "full_name": f"User {i}"})
            r.raise_for_status()
            # duplicate signup -> 400
            c.post(f"{API}/users/signup", json={"email": a.email, "password": a.password})
            _login(c, a)
            c.get(f"{API}/users/me", headers=a.h)
            c.patch(f"{API}/users/me", json={"full_name": f"User {i} ({rng.choice(WORDS)})"}, headers=a.h)
            ids = []
            for _ in range(rng.randint(3, 8)):
                body = {"title": _title(rng)}
                if rng.random() < 0.6:
                    body["description"] = " ".join(rng.sample(WORDS, 5))
                ids.append(c.post(f"{API}/items/", json=body, headers=a.h).json()["id"])
            item_ids[a.email] = ids
            actors.append(a)

        for a in actors:
            ids = item_ids[a.email]
            c.get(f"{API}/items/", headers=a.h)
            c.get(f"{API}/items/", params={"skip": 0, "limit": 2}, headers=a.h)
            c.get(f"{API}/items/", params={"skip": 2, "limit": 2}, headers=a.h)
            c.get(f"{API}/items/{rng.choice(ids)}", headers=a.h)
            target = rng.choice(ids)
            c.put(f"{API}/items/{target}", json={"description": "updated: " + _title(rng)}, headers=a.h)
            c.get(f"{API}/items/{target}", headers=a.h)
            gone = ids.pop()
            c.delete(f"{API}/items/{gone}", headers=a.h)
            c.get(f"{API}/items/{gone}", headers=a.h)  # 404
            other = rng.choice([x for x in actors if x is not a] or [a])
            if item_ids[other.email]:
                c.get(f"{API}/items/{item_ids[other.email][0]}", headers=a.h)  # 403
            c.post(f"{API}/items/", json={"description": "no title"}, headers=a.h)  # 422
            c.get(f"{API}/items/")  # 401
            c.get(f"{API}/items/{uuid.uuid4()}", headers=a.h)  # 404

        # password change then login with the new password
        a = actors[0]
        new_pw = a.password + "-v2"
        c.patch(f"{API}/users/me/password", json={"current_password": a.password, "new_password": new_pw},
                headers=a.h)
        c.post(f"{API}/login/access-token", data={"username": a.email, "password": a.password})  # 400
        a.password = new_pw
        _login(c, a)
        c.get(f"{API}/users/me", headers=a.h)

        # admin views
        c.get(f"{API}/users/", params={"skip": 0, "limit": 5}, headers=admin.h)
        c.get(f"{API}/items/", headers=admin.h)
        c.get(f"{API}/items/", params={"skip": 0, "limit": 10}, headers=admin.h)
        me = c.get(f"{API}/users/me", headers=admin.h).json()
        c.get(f"{API}/users/{me['id']}", headers=admin.h)
        ops = Actor(f"ops-{tag}@replay.example.com", f"ops-{tag}-{rng.randint(1000, 9999)}")
        u = c.post(f"{API}/users/", json={"email": ops.email, "password": ops.password, "full_name": "Ops"},
                   headers=admin.h).json()
        c.get(f"{API}/users/{u['id']}", headers=admin.h)
        c.patch(f"{API}/users/{u['id']}", json={"full_name": "Ops (renamed)"}, headers=admin.h)
        c.get(f"{API}/users/{u['id']}", headers=actors[1].h)  # 403 for non-admin
        c.delete(f"{API}/users/{u['id']}", headers=admin.h)
        c.get(f"{API}/users/{u['id']}", headers=admin.h)  # 404

        # last user leaves
        c.delete(f"{API}/users/me", headers=actors[-1].h)
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m replay.generate", description=__doc__.split("\n")[0])
    p.add_argument("--base-url", required=True)
    p.add_argument("--users", type=int, default=6)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    counts = run(args.base_url, args.users, args.seed,
                 os.environ["EA_USERNAME"], os.environ["EA_PASSWORD"])
    print(f"sent {counts['requests']} requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
