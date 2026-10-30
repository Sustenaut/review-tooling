"""python -m review_tooling.facts gather [--adopt] | check | get PATH | lint (see review_tooling/facts/__init__.py)."""

from __future__ import annotations

import subprocess
import sys

from review_tooling import facts


def main(argv: list[str]) -> int:
    match argv:
        case ["gather"]:
            from review_tooling.facts import gather

            gather.gather()
            return 0
        case ["get", path]:
            from review_tooling.facts.gather import pub_binary

            from review_tooling.facts.gather import require_pub

            return subprocess.run([require_pub(pub_binary()), "get", path, f"--dir={facts.OBJECTS}"]).returncode
        case ["gather", "--adopt"]:
            from review_tooling.facts import gather

            gather.gather(sign=False)
            return 0
        case ["check"]:
            from review_tooling.facts import gather

            problems = gather.check()
            for problem in problems:
                print(problem)
            print(f"{len(problems)} problem(s): run `make gather`" if problems
                  else "corpus/ agrees with the registry")
            return 1 if problems else 0
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
    from review_tooling.facts.gather import PubVersionError

    try:
        sys.exit(main(sys.argv[1:]))
    except PubVersionError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
