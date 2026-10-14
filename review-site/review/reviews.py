"""Suggestions, and what reviewers did with them, read from and written to the store."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from review import drafts as d
from review.auth import ASSURANCE, Reviewer
from review.signer import Signer
from review.store import Exists, Store, object_path, read_json

ACTIONS = {
    "agree": "Agreed and signed",
    "edit": "Edited and signed",
    "split": "Split and signed",
    "reject": "Not the same",
    "defer": "Not sure yet",
}


class Invalid(Exception):
    """The form asks for something that can't be signed as it stands."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def suggestions(store: Store) -> list[dict]:
    out = [read_json(store, p) for p in store.list("suggestions/") if p.endswith(".json")]
    return [s for s in out if s]


def suggestion(store: Store, sid: str) -> dict | None:
    return next((s for s in suggestions(store) if s["id"] == sid), None)


def reviews(store: Store) -> list[dict]:
    out = [read_json(store, p) for p in store.list("reviews/") if p.endswith(".json")]
    return sorted((r for r in out if r), key=lambda r: r["created"])


def status(reviewed: list[dict]) -> str:
    """A suggestion's state: its latest review that decided something, or open."""
    decided = [r for r in reviewed if r["action"] != "defer"]
    if decided:
        return decided[-1]["action"]
    return "defer" if reviewed else "open"


def queue(store: Store) -> list[dict]:
    """Open and deferred suggestions first, by priority, then the decided ones."""
    by_suggestion: dict[str, list[dict]] = {}
    for r in reviews(store):
        by_suggestion.setdefault(r["suggestion"], []).append(r)
    rows = []
    for s in suggestions(store):
        state = status(by_suggestion.get(s["id"], []))
        rows.append({**s, "status": state, "reviews": by_suggestion.get(s["id"], [])})
    return sorted(rows, key=lambda s: (s["status"] not in ("open", "defer"), -s.get("priority", 0), s["created"]))


def overturned(store: Store) -> dict[str, tuple[int, int]]:
    """Per suggester (model and version): (rejected, decided). Spec Section 7.4 asks that the
    rate at which triage is overturned be published; a low rate may mean rejecting costs too
    much, not that the suggester is right."""
    engines = {s["id"]: f"{s['engine']['model']} {s['engine']['version']}" for s in suggestions(store)}
    decided: Counter = Counter()
    rejected: Counter = Counter()
    latest: dict[str, str] = {}
    for r in reviews(store):
        if r["action"] != "defer":
            latest[r["suggestion"]] = r["action"]
    for sid, action in latest.items():
        engine = engines.get(sid, "unknown")
        decided[engine] += 1
        rejected[engine] += action == "reject"
    return {e: (rejected[e], decided[e]) for e in decided}


def _form_concept(form: dict, suffix: str, draft: dict) -> d.Concept:
    def one(name: str, default: str = "") -> str:
        return (form.get(name + suffix) or [default])[0].strip()
    return d.Concept(
        pdc=one("pdc", draft.get("pdc", "") if not suffix else ""),
        name=one("name", draft.get("name", "") if not suffix else ""),
        kind=draft["kind"],
        includes=d.lines(one("includes")),
        excludes=d.lines(one("excludes")),
    )


def plan(s: dict, action: str, form: dict) -> tuple[list[d.Draft], list[dict], dict]:
    """What `action` on suggestion `s` signs: (drafts, the concepts it frames, changes made)."""
    if action not in ACTIONS:
        raise Invalid(f"no action {action!r}")
    candidate = s.get("candidate", {})
    if action == "reject":
        reason = (form.get("reason") or [""])[0].strip()
        if not reason:
            raise Invalid("Say why the grouping is wrong: the reason is the claim the dispute names.")
        return d.rejection(reason, s["id"]), [], {"reason": reason}
    if action == "defer":
        return [], [], {"note": (form.get("note") or [""])[0].strip()}
    if s["finding"] == "quoted-from":
        if action != "agree":
            raise Invalid("A quoted figure is agreed or rejected; there is nothing to edit or split.")
        return [d.Relate("derived-from", s["target"], candidate["primary"])], [], {}

    draft = candidate["concept"]
    members = [m["cid"] for m in candidate["members"]]
    if action == "agree":
        concept = d.Concept(draft["pdc"], draft["name"], draft["kind"], list(draft["includes"]),
                            list(draft["excludes"]), [m["cid"] for m in candidate["members"] if m.get("in")])
        concepts = [("concept", concept)]
    else:
        side = {m: (form.get(f"member-{i}") or ["out"])[0] for i, m in enumerate(members)}
        first = _form_concept(form, "", draft)
        first.members = [m for m in members if side[m] == "a"]
        concepts = [("concept", first)]
        if action == "split":
            second = _form_concept(form, "-2", draft)
            second.members = [m for m in members if side[m] == "b"]
            if not second.name or not second.pdc or not second.members:
                raise Invalid("A split needs the second concept's name, PDC code and at least one member.")
            concepts.append(("concept-2", second))
    for _, c in concepts:
        if not c.name or not c.pdc:
            raise Invalid("A concept needs a name and a PDC code.")
        if not c.includes:
            raise Invalid("A concept needs at least one line saying what it includes.")
    drafts = [x for ref, c in concepts for x in d.concept_drafts(c, ref)]
    framed = [{"ref": ref, "pdc": c.pdc, "name": c.name, "kind": c.kind, "includes": c.includes,
               "excludes": c.excludes, "members": c.members} for ref, c in concepts]
    changes = {}
    if action != "agree":
        original = {"name": draft["name"], "pdc": draft["pdc"], "includes": draft["includes"],
                    "excludes": draft["excludes"],
                    "members": [m["cid"] for m in candidate["members"] if m.get("in")]}
        mine = {k: framed[0][k] for k in original}
        changes = {k: {"suggested": original[k], "signed": mine[k]} for k in original if original[k] != mine[k]}
    return drafts, framed, changes


def record(store: Store, signer: Signer, reviewer: Reviewer, s: dict, action: str, form: dict) -> dict:
    """Sign what the action asks for, store the objects and the review, and return the review."""
    drafts, framed, changes = plan(s, action, form)
    created = now()
    made, key = [], None
    if drafts:
        signed = signer.sign(reviewer, drafts, created)
        key, made = signed.key, signed.made
        for cid, data in signed.objects.items():
            try:
                store.create(object_path(cid), data, "application/cbor")
            except Exists:
                pass  # the same bytes under the same CID: already kept
        refs = dict(made)
        for c in framed:
            c["cid"] = refs.get(c["ref"])
    review = {
        "suggestion": s["id"], "action": action, "created": created,
        "key": key, "assurance": ASSURANCE if key else None, "login": reviewer.login,
        "made": made, "concepts": framed, "changes": changes,
    }
    name = f"reviews/{created.replace(':', '')}-{s['id'].rsplit(':', 1)[-1][:12]}-{action}.json"
    store.create(name, json.dumps(review, indent=1).encode(), "application/json")
    return review


def concepts_at(store: Store, pdc: str) -> list[dict]:
    """Every signed concept at a PDC code, oldest first, with the review that made it. More
    than one lineage at a code is contested (spec Section 5.9.1), and both are shown."""
    out = []
    for r in reviews(store):
        for c in r.get("concepts", []):
            if c["pdc"] == pdc:
                out.append({**c, "review": r})
    return out
