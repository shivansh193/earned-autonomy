"""ea-tfplan: terraform show -json plan.out > plan.json; ea-tfplan plan.json > infra.json"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tfplan.parse import analyse


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        sys.stderr.write("usage: ea-tfplan plan.json\n")
        return 2
    json.dump(analyse(json.loads(Path(args[0]).read_text(encoding="utf-8"))), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
