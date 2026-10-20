"""Traffic file format: one JSON object per line (an *exchange*).

```
{"seq": 3, "method": "GET", "path": "/api/v1/items/", "query": "skip=0&limit=100",
 "content_type": null, "body": null, "auth_ref": 1,
 "status": 200, "response": {...}, "latency_ms": 12.3}
```

* ``body`` is the request body: parsed JSON, a form ``{field: value}`` dict
  (``content_type`` = ``application/x-www-form-urlencoded``) or text.
* ``auth_ref`` is the ``seq`` of the exchange whose response issued the bearer
  token this request used (``0`` = a token issued before recording started,
  ``null`` = no auth).
* secrets are never stored: they are replaced by ``${NAME}`` placeholders
  (see :mod:`replay.sanitize`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class Exchange:
    seq: int
    method: str
    path: str
    query: str = ""
    content_type: str | None = None
    body: Any = None
    auth_ref: int | None = None
    status: int = 0
    response: Any = None
    latency_ms: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Exchange:
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in fields})


def load(path: Path) -> list[Exchange]:
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(Exchange.from_dict(json.loads(line)))
    return sorted(out, key=lambda e: e.seq)


def save(path: Path, exchanges: list[Exchange]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for ex in exchanges:
            fh.write(ex.to_json() + "\n")
