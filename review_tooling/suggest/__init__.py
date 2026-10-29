"""The suggestion job: machine suggestions of concepts for the review service.

Extracts (corpus/sources/, publet spec Section 5.9.8) say what one paper
printed; they carry no concept. This job proposes which of them measure the
same thing, as `triage` annotations (spec Section 7.4) that people at
review.sustenaut.eu agree with, edit, split or reject. A suggestion signs
nothing on anyone's behalf: the concept and its memberships are unsigned
drafts in the triage's `candidate` blob until a reviewer signs them (R14).

    python -m tools.suggest run --state=DIR --out=DIR   # one run (run.py)
    python -m tools.suggest key                         # make the suggester key for MODEL (once)

Without an API key, a Claude Code session drafts instead (`make suggest-local`):
`requests` writes each group's request body to a file, the session writes
`<kind>.draft.json` beside each in the request's output schema, and
`run --drafts=DIR` signs those exactly as it would the API's answers. The
request bodies are the same, so the prompt blob discloses what was asked.

A run, in order:

1. Snapshot: every gathered extract (corpus/sources/gathered.json), with
   what a reader needs to judge it. Its canonical JSON is the `inputs` blob;
   when a run with the same inputs is already recorded, nothing happens.
2. Group: extracts not already in an open or agreed suggestion, by kind,
   since only values of one kind can measure one concept.
3. Draft: one Claude call per group, with the PDC (the UDC Summary at
   PDC_REV) and the concepts already drafted, returning concepts with each
   member in or out and why (draft.py).
4. Sign: one triage per concept, signed by the suggester key for MODEL,
   and write what the bucket gets: `suggestions/<id>.json` in the shape the
   review service reads (review-site/fixtures/suggestions/), the objects,
   and the prompt, inputs and candidate blobs (sign.py).

`quoted-from` suggestions wait until a lead's citation names a source the
registry holds; none of the first four papers cites another.

`engine.model` stays the same for a suggester key, because the review
service counts overturned suggestions per engine. A different model gets
its own key, so the run uses no refusal fallback to another model: a
refused group is skipped and reported.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tools import facts, sources

ROOT = facts.ROOT
DIR = ROOT / "corpus" / "suggest"
KEYS = DIR / "keys.json"

MODEL = os.environ.get("SUGGEST_MODEL", "claude-opus-5-5")
EFFORT = "high"
# The PDC the suggestions are coded in: schryer/publet-decimal-classification,
# the same commit pub-site/Dockerfile serves.
PDC_REV = "6f0f3072701a643fd8516e8551314677954713af"
PDC_URL = (f"https://raw.githubusercontent.com/schryer/publet-decimal-classification/{PDC_REV}"
           "/data/pdc/udc-summary.json")


class SuggestError(Exception):
    pass


# --- identifiers and canonical bytes ------------------------------------------


def cid(data: bytes) -> str:
    """The identifier of a blob or object: SHA-256 of the exact bytes (spec Section 4.7)."""
    return "pub:sha2-256:" + base64.b32encode(hashlib.sha256(data).digest()).decode().lower().rstrip("=")


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def filename(ident: str) -> str:
    return ident.replace(":", "_")


# --- 1. the snapshot ---------------------------------------------------------


def shown(value: dict) -> str:
    if "low" in value:
        return f"{value['low']} to {value['high']}"
    if set(value) == {"high"}:
        return f"at most {value['high']}"
    return " ± ".join(value[k] for k in ("value", "half_width") if k in value)


def snapshot(reg: dict, gathered: dict) -> list[dict]:
    """Every gathered extract, by id, with what a reader judges it by."""
    items = gathered["items"]
    srcs = {s["id"]: s for s in reg["sources"]}
    systems = {s["id"]: s for s in reg["systems"]}
    scenarios = {s["id"]: s for s in reg["scenarios"]}
    kinds = {d["id"]: d["defines"] for d in reg["definitions"]}
    out = []
    for e in reg["extracts"]:
        if e["id"] not in items:
            continue
        system = systems.get(e["about"], {})
        out.append({
            "id": e["id"],
            "cid": items[e["id"]]["cid"],
            "source": srcs[e["source"]]["short"],
            "source_type": srcs[e["source"]]["source_type"],
            "system": system.get("name"),
            "system_class": system.get("class"),
            "quantity": e["quantity"],
            "kind": e["kind"],
            "kind_cid": items[e["kind"]]["cid"],
            "kind_name": kinds[e["kind"]]["name"],
            "value": shown(e["value"]),
            "unit": e["unit"],
            "locator": e["locator"],
            "quote": e["quote"],
            "scope": e["scope"],
            "scenario": scenarios[e["scenario"]]["content"] if e.get("scenario") else None,
            "cites": e.get("cites"),
        })
    return out


def title(x: dict) -> str:
    about = f" of {x['system']}" if x["system"] else ""
    return f"{x['source']}: {x['quantity']}{about}, {x['value']} {x['unit']}"


# --- 2. what is already suggested, and the groups ----------------------------


def read_dir(root: Path, prefix: str) -> list[dict]:
    base = root / prefix
    return [json.loads(p.read_text()) for p in sorted(base.glob("*.json"))] if base.is_dir() else []


@dataclass
class State:
    """What the review bucket already holds: suggestions, reviews and recorded runs."""

    suggestions: list[dict]
    reviews: list[dict]
    runs: set[str]

    @classmethod
    def read(cls, root: Path) -> "State":
        runs = {p.stem for p in (root / "runs").glob("*.json")} if (root / "runs").is_dir() else set()
        return cls(read_dir(root, "suggestions"), read_dir(root, "reviews"), runs)

    def status(self, sid: str) -> str:
        """As the review service has it (review-site/review/reviews.py): the latest
        review that decided something, else open."""
        decided = sorted((r for r in self.reviews if r["suggestion"] == sid and r["action"] != "defer"),
                         key=lambda r: r["created"])
        return decided[-1]["action"] if decided else "open"

    def covered(self) -> set[str]:
        """Extracts already in a suggestion nobody has rejected: suggesting them again
        would only ask the same question twice."""
        out: set[str] = set()
        for s in self.suggestions:
            if s["finding"] == "groups-under" and self.status(s["id"]) != "reject":
                out |= {m["cid"] for m in s["candidate"]["members"] if m.get("in")}
        return out

    def concepts(self) -> list[dict]:
        """Concepts drafted or signed so far: {pdc, name, status}."""
        out = []
        for s in self.suggestions:
            if s["finding"] == "groups-under":
                c = s["candidate"]["concept"]
                out.append({"pdc": c["pdc"], "name": c["name"], "status": self.status(s["id"])})
        return out

    def codes(self) -> set[str]:
        """Every PDC code in use, in suggestions or in what reviewers signed."""
        out = {c["pdc"] for c in self.concepts()}
        for r in self.reviews:
            out |= set(re.findall(r'"pdc": ?"([^"]+)"', json.dumps(r)))
        return out


def groups(snap: list[dict], covered: set[str]) -> dict[str, list[dict]]:
    """Unclassified extracts by kind."""
    out: dict[str, list[dict]] = {}
    for x in snap:
        if x["cid"] not in covered:
            out.setdefault(x["kind"], []).append(x)
    return out


# --- the PDC -----------------------------------------------------------------


def pdc_classes(path: Path | None = None) -> dict[str, str]:
    """PDC code to name, from the UDC Summary import at PDC_REV."""
    if path is None:
        path = ROOT / ".pdc" / f"udc-summary-{PDC_REV[:12]}.json"
        if not path.exists():
            path.parent.mkdir(exist_ok=True)
            with urllib.request.urlopen(PDC_URL, timeout=60) as r:
                path.write_bytes(r.read())
    data = json.loads(path.read_text())
    return {c["pdc"]: c["claim"]["defines"]["name"] for c in data["classes"]}


def registry_concepts(reg: dict) -> dict[str, str]:
    return {d["defines"]["pdc"]: d["defines"]["name"] for d in reg["definitions"]
            if d["defines"]["sort"] == "concept"}


def next_code(parent: str, used: set[str]) -> str:
    """The first free PDC extension under a UDC class: `parent~N` (the separator `~`
    is not a UDC character, so an extension never collides with a UDC code)."""
    n = 1
    while f"{parent}~{n}" in used:
        n += 1
    return f"{parent}~{n}"


# --- 4. a draft becomes suggestions ------------------------------------------


def suggestions_for(group: list[dict], draft: dict, pdc: dict[str, str], used: set[str]) -> tuple[list[dict], list[str]]:
    """Turn one group's draft into suggestion bodies (without the triage, which sign.py
    adds), and the problems with what was skipped. `used` gains each code assigned."""
    by_id = {x["id"]: x for x in group}
    taken: set[str] = set()
    out, problems = [], []
    for c in draft["concepts"]:
        if c["parent"] not in pdc:
            problems.append(f"{c['name']}: {c['parent']} is not a PDC class; skipped")
            continue
        members = []
        for m in c["members"]:
            x = by_id.get(m["id"])
            if x is None:
                problems.append(f"{c['name']}: {m['id']} is not in this group; dropped")
                continue
            if m["in"] and x["id"] in taken:
                problems.append(f"{c['name']}: {m['id']} is already in another concept; shown as out")
                m = {**m, "in": False}
            members.append({"cid": x["cid"], "title": title(x), "locator": x["locator"], "unit": x["unit"],
                            "in": bool(m["in"]), "lead": bool(x["cites"]), "why": m["why"]})
            if m["in"]:
                taken.add(x["id"])
        inside = [m for m in members if m["in"]]
        if not inside:
            problems.append(f"{c['name']}: no member is in; skipped")
            continue
        code = next_code(c["parent"], used)
        used.add(code)
        kind = group[0]
        concept = {"pdc": code, "name": c["name"], "kind": kind["kind_cid"], "kind_name": kind["kind_name"],
                   "unit": c["unit"], "includes": c["includes"], "excludes": c["excludes"]}
        out.append({
            "finding": "groups-under",
            "title": c["name"],
            "priority": min(5, len({m["title"].split(":")[0] for m in inside})),
            "target": inside[0]["cid"],
            "refs": [m["cid"] for m in members],
            "candidate": {"reason": c["reason"], "concept": concept, "members": members},
        })
    return out, problems


def candidate_blob(suggestion: dict) -> bytes:
    """The unsigned drafts a reviewer would sign as they stand: the concept's definition
    and a `classifies` per member in (review-site/review/drafts.py makes the same)."""
    c = suggestion["candidate"]["concept"]
    return canonical_json({
        "concept": {"class": "definitional", "scope": "unconditional",
                    "defines": {"sort": "concept", "pdc": c["pdc"], "name": c["name"], "kind": c["kind"],
                                "boundary": {"includes": c["includes"], "excludes": c["excludes"]}}},
        "classifies": [m["cid"] for m in suggestion["candidate"]["members"] if m["in"]],
    })


def keys() -> dict:
    return json.loads(KEYS.read_text()) if KEYS.exists() else {}


def registry() -> dict:
    return sources.registry()


def gathered() -> dict:
    return json.loads(sources.GATHERED.read_text())
