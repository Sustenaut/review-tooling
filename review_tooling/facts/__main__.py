"""python -m tools.facts gather | get PATH | lint (see tools/facts/__init__.py)."""

from __future__ import annotations

import subprocess
import sys

from tools import facts


def main(argv: list[str]) -> int:
    match argv:
        case ["gather"]:
            from tools.facts import gather

            gather.gather()
            return 0
        case ["get", path]:
            from tools.facts.gather import pub_binary

            return subprocess.run([pub_binary(), "get", path, f"--dir={facts.OBJECTS}"]).returncode
        case ["lint"]:
            found = facts.lint(facts.registry())
            for d in found:
                print(d)
            print(f"{len(found)} disagreement(s) with the facts registry" if found
                  else "prose agrees with the facts registry")
            return 1 if found else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
