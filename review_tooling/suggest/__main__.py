"""python -m tools.suggest run --state=DIR --out=DIR | key (see tools/suggest/__init__.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    match argv:
        case ["run", *rest] if {"state", "out"} <= {a[2:].split("=", 1)[0] for a in rest}:
            from tools.suggest.run import run

            opts = dict(a[2:].split("=", 1) for a in rest)
            record = run(Path(opts["state"]), Path(opts["out"]))
            print(json.dumps(record | {"suggestions": len(record.get("suggestions", []))}, indent=1))
            return 0
        case ["key"]:
            from tools.facts.gather import now
            from tools.suggest.sign import make_key, secret_path

            key = make_key(now())
            print(f"key {key}\nsecret {secret_path()}\n"
                  "Commit corpus/suggest/, and store the secret in Bitwarden and as the SUGGESTER_KEY "
                  "repository secret.")
            return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
