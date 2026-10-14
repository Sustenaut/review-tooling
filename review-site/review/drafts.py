"""What a reviewer's action asks to be signed, as drafts the signer turns into objects.

A draft names earlier drafts by a local `ref` ("concept", "concept-2",
"reason"), since their CIDs exist only once composed; everything else is
named by CID.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Compose:
    ref: str
    cls: str
    content: str
    defines: dict | None = None
    depends: tuple[str, ...] = ()


@dataclass(frozen=True)
class Classify:
    """`classifies`: `target` is a member of the concept `subject` (spec Section 7.1)."""

    target: str
    subject: str  # a ref


@dataclass(frozen=True)
class Relate:
    kind: str
    source: str  # a ref, or a CID
    target: str  # a CID


Draft = Compose | Classify | Relate


@dataclass
class Concept:
    """A concept as a reviewer frames it: the suggestion's draft, or their edit of it."""

    pdc: str
    name: str
    kind: str
    includes: list[str]
    excludes: list[str]
    members: list[str] = field(default_factory=list)

    def defines(self) -> dict:
        return {"sort": "concept", "pdc": self.pdc, "name": self.name, "kind": self.kind,
                "boundary": {"includes": self.includes, "excludes": self.excludes}}

    def content(self) -> str:
        lines = [f"{self.name}: what the measurements under PDC {self.pdc} measure."]
        if self.includes:
            lines.append("Includes: " + "; ".join(self.includes) + ".")
        if self.excludes:
            lines.append("Excludes: " + "; ".join(self.excludes) + ".")
        return " ".join(lines)


def concept_drafts(concept: Concept, ref: str) -> list[Draft]:
    out: list[Draft] = [Compose(ref, "definitional", concept.content(), concept.defines())]
    out += [Classify(member, ref) for member in concept.members]
    return out


def rejection(reason: str, suggestion: str) -> list[Draft]:
    """"Not the same": a claim stating why, and a `disputes` relation from it against the
    suggestion, which spec Section 7.4 says costs no more than any other dispute."""
    return [Compose("reason", "normative", reason.strip()),
            Relate("disputes", "reason", suggestion)]


def lines(text: str) -> list[str]:
    """A textarea's lines, without blanks: one boundary line each."""
    return [line.strip() for line in text.splitlines() if line.strip()]
