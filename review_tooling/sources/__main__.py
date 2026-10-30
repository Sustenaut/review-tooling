"""python -m review_tooling.sources add DOI PDF | lint | review [--port=N] | gather | check | fetch | upload (see review_tooling/sources/__init__.py)."""

from __future__ import annotations

import sys

from review_tooling import sources


def main(argv: list[str]) -> int:
    match argv:
        case ["add", doi, pdf]:
            from review_tooling.sources import add

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
            from review_tooling.sources import review

            port = next((int(a.split("=", 1)[1]) for a in rest if a.startswith("--port=")), 8765)
            review.serve(port)
            return 0
        case ["gather"]:
            from review_tooling.sources import gather

            gather.gather()
            return 0
        case ["fetch"]:
            from review_tooling.sources import gather

            recorded = gather.load_gathered()
            gather.fetch(recorded, gather.OBJECTS)
            print(f"{gather.OBJECTS} holds the {len(recorded['objects'])} gathered object(s)")
            return 0
        case ["check"]:
            from review_tooling.sources import gather

            problems = gather.check()
            for problem in problems:
                print(problem)
            print(f"{len(problems)} problem(s): run `make sources-gather` and `make sources-upload`" if problems
                  else "corpus/sources agrees with gathered.json and storage")
            return 1 if problems else 0
        case ["upload"]:
            from review_tooling.sources import upload

            upload.upload()
            return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    from review_tooling.facts.gather import PubVersionError

    try:
        sys.exit(main(sys.argv[1:]))
    except PubVersionError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
