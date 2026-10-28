"""python -m tools.sources add DOI PDF | lint | review [--port=N] | gather | check | upload (see tools/sources/__init__.py)."""

from __future__ import annotations

import sys

from tools import sources


def main(argv: list[str]) -> int:
    match argv:
        case ["add", doi, pdf]:
            from tools.sources import add

            print(add.add(doi, pdf))
            return 0
        case ["lint"]:
            problems = sources.lint(sources.registry())
            for problem in problems:
                print(problem)
            reg = sources.registry()
            counts: dict[str, int] = {}
            for e in reg["extracts"]:
                counts[sources.status(e)] = counts.get(sources.status(e), 0) + 1
            print(f"{len(problems)} problem(s); {len(reg['extracts'])} extract(s): "
                  + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
            return 1 if problems else 0
        case ["review", *rest]:
            from tools.sources import review

            port = next((int(a.split("=", 1)[1]) for a in rest if a.startswith("--port=")), 8765)
            review.serve(port)
            return 0
        case ["gather"]:
            from tools.sources import gather

            gather.gather()
            return 0
        case ["check"]:
            from tools.sources import gather

            problems = gather.check()
            for problem in problems:
                print(problem)
            print(f"{len(problems)} problem(s): run `make sources-gather` and `make sources-upload`" if problems
                  else "corpus/sources agrees with gathered.json and storage")
            return 1 if problems else 0
        case ["upload"]:
            from tools.sources import upload

            upload.upload()
            return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
