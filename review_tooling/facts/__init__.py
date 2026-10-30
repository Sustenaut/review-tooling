"""Sustenaut's facts registry: one particular thing per fact, found by path.

`corpus/registry.json` holds the definitions, singulars and facts; `make
gather` composes and signs them into `corpus/objects/` with a `pub` built
from publet (see gather.py). Two commands read the result:

    python -m review_tooling.facts get place.site.example.room.floor_area
    python -m review_tooling.facts lint

`get` is `pub get` over corpus/objects/. `lint` reads the registry itself,
so it runs anywhere, CI included, without pub or the signing key: it looks
in the prose of plan-doc/, plans/ and opportunities/ for a number with a
unit on the same line as something a fact is about, and reports where the
number disagrees with the fact.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# The repository holding corpus/: where the tools run, or $SOURCES_ROOT. The data
# stays private there; this package holds none of it.
ROOT = Path(os.environ.get("SOURCES_ROOT") or Path.cwd()).resolve()
REGISTRY = ROOT / "corpus" / "registry.json"
OBJECTS = ROOT / "corpus" / "objects"
PROSE = ("plan-doc", "plans", "opportunities")

# How each unit the registry uses is written in prose. Matched after a
# number, with optional space, and not followed by a letter.
UNIT_SPELLINGS = {
    "m2": r"(?:m²|m2|sq\.? ?m|square met(?:re|er)s?)",
    "har": r"(?:ha|hectares?)",
    "A": r"(?:A|amps?)",
    "mm": r"(?:mm)",
    "m": r"(?:m)",
    "[EUR]": None,  # written before the number: €5
}
NUMBER = r"(\d+(?:[.,]\d+)?)"


def registry(path: Path = REGISTRY) -> dict:
    return json.loads(path.read_text())


@dataclass(frozen=True)
class Stated:
    """A fact's value as the registry states it, with how to check prose."""

    fact: str
    content: str
    unit: str
    low: float | None
    high: float | None
    mentions: tuple[str, ...]

    def agrees(self, number: float) -> bool:
        if self.low is not None and number < self.low:
            return False
        return not (self.high is not None and number > self.high)

    def shown(self) -> str:
        if self.low == self.high:
            return f"{self.low:g} {self.unit}"
        return f"at most {self.high:g} {self.unit}"


def stated(reg: dict) -> list[Stated]:
    out = []
    for f in reg["facts"]:
        value = f["value"]
        if isinstance(value, dict):
            low = value.get("low", value.get("value"))
            high = value.get("high", value.get("value"))
        else:
            low = high = value
        out.append(Stated(f["id"], f["content"], f["unit"],
                          None if low is None else float(low),
                          None if high is None else float(high),
                          tuple(f.get("mentions", ()))))
    return out


def _quantities(line: str, unit: str) -> list[tuple[int, float, str]]:
    """Each number written in `unit` on the line: (position, number, text)."""
    spelled = UNIT_SPELLINGS.get(unit)
    if spelled is None and unit == "[EUR]":
        pattern = re.compile(r"(?:€\s?|EUR\s?)" + NUMBER)
    elif spelled:
        pattern = re.compile(r"(?<![\w.,])" + NUMBER + r"\s?" + spelled + r"(?![\w²])")
    else:
        return []
    return [(m.start(), float(m.group(1).replace(",", ".")), m.group(0)) for m in pattern.finditer(line)]


def _gap(start: int, length: int, pos: int, width: int) -> tuple[int, int]:
    """How far a mention is from a number: the characters between them, a
    mention before the number winning a tie ("the room is 50 m²")."""
    if start + length <= pos:
        return (pos - start - length, 0)
    return (max(start - pos - width, 0), 1)


@dataclass(frozen=True)
class Disagreement:
    where: str
    line: int
    text: str
    mention: str
    fact: Stated

    def __str__(self) -> str:
        return (f"{self.where}:{self.line}: \"{self.text}\" near \"{self.mention}\" disagrees with "
                f"{self.fact.fact} = {self.fact.shown()} ({self.fact.content})")


def lint(reg: dict, root: Path = ROOT, dirs: tuple[str, ...] = PROSE) -> list[Disagreement]:
    """Numbers in prose that disagree with the fact about the thing beside them.

    A number is checked against the fact whose mention is nearest to it on
    the same line, among the facts in the number's unit, so a line naming
    two rooms checks each area against its own room. A line that quotes a
    superseded figure on purpose is listed in the registry's `lint_allow`,
    with why.
    """
    facts = stated(reg)
    allowed = {(a["file"], a["text"]) for a in reg.get("lint_allow", [])}
    found = []
    for d in dirs:
        for path in sorted((root / d).rglob("*.md")):
            for n, line in enumerate(path.read_text().splitlines(), start=1):
                lowered = line.lower()
                for unit in {f.unit for f in facts}:
                    candidates = [(f, m, lowered.find(m.lower())) for f in facts if f.unit == unit
                                  for m in f.mentions if m.lower() in lowered]
                    if not candidates:
                        continue
                    for pos, number, text in _quantities(line, unit):
                        fact, mention, _ = min(candidates, key=lambda c: _gap(c[2], len(c[1]), pos, len(text)))
                        where = str(path.relative_to(root))
                        if not fact.agrees(number) and (where, text) not in allowed:
                            found.append(Disagreement(where, n, text, mention, fact))
    return found
