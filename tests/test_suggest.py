"""The suggestion job (tools/suggest/): grouping, drafting into the review service's
shape, and signing triage annotations. Claude is never called: a fake client
answers. The signing test needs a pub built from publet-cli v0.2.2 or later
(`make pub`, or PUB=...) and is skipped without one."""

from __future__ import annotations

import json
import os
import shutil
import types
from pathlib import Path

import pytest

from review_tooling import suggest as sg
from review_tooling.suggest import draft, run as job, sign

FIXTURE = Path(__file__).resolve().parents[1] / "review-site" / "fixtures" / "suggestions" / "led-shelf.json"
PDC = {"631": "Agriculture in general", "631.5": "Agricultural operations", "628.9": "Lighting"}


def test_canonical_cbor_is_what_pub_writes():
    # An assessment annotation `pub annotate` (v0.2.2) wrote, and its identifier.
    obj = {"author": "pub:sha2-256:pc447lywkwqgyv7angd5w5oil6izjqyykjpfg74ausidcsbkdnia",
           "body": {"kind": "assessment", "scope": {"conditions": [], "domain": "unconditional"},
                    "target": "pub:sha2-256:refm265o2ymrgxn63qz2unh5tgw24ifyeuxfjedmuxi4ltwhqszq",
                    "value": {"grounds": "g", "verdict": "undetermined"}},
           "created": "2026-10-29T00:00:00Z", "pub": "1", "type": "claim.annotation"}
    assert sg.cid(sign.cbor(obj)) == "pub:sha2-256:ouq3urttsxfux67ttdjwjgpvirfxedvrqehutxwudyruifoy7pyq"
    with pytest.raises(TypeError):
        sign.cbor(1.5)


def test_the_snapshot_holds_every_gathered_extract_by_kind():
    reg, gathered = sg.registry(), sg.gathered()
    snap = sg.snapshot(reg, gathered)
    extracts = [e for e in reg["extracts"] if e["id"] in gathered["items"]]
    assert len(snap) == len(extracts) > 0
    assert all(x["cid"] == gathered["items"][x["id"]]["cid"] for x in snap)
    groups = sg.groups(snap, set())
    assert sum(len(g) for g in groups.values()) == len(snap)
    assert all(len({x["kind"] for x in g}) == 1 for g in groups.values())
    assert sum(len(g) for g in sg.groups(snap, {snap[0]["cid"]}).values()) == len(snap) - 1


def _group(n: int = 3) -> list[dict]:
    return [{"id": f"e.{i}", "cid": f"pub:sha2-256:{'a' * 51}{i}", "source": f"Author {i} (2025)",
             "system": "a farm", "quantity": "yield", "value": str(i), "unit": "kg/m2", "locator": f"p {i}",
             "cites": "someone" if i == 2 else None, "kind": "k.yield", "kind_cid": "pub:sha2-256:kind",
             "kind_name": "yield per area"} for i in range(n)]


def _concept(parent: str, members: list[tuple[str, bool]]) -> dict:
    return {"parent": parent, "name": "Lettuce yield", "unit": "kg/m2", "includes": ["lettuce"],
            "excludes": ["herbs"], "reason": "same crop and stage",
            "members": [{"id": i, "in": inside, "why": "w"} for i, inside in members]}


def test_a_draft_becomes_suggestions_in_the_review_services_shape():
    used = {"631.5~1"}
    made, problems = sg.suggestions_for(_group(), {"concepts": [
        _concept("631.5", [("e.0", True), ("e.1", False), ("e.2", True)]),
        _concept("631.5", [("e.0", True), ("e.1", True)]),   # e.0 is taken: shown as out
        _concept("999", [("e.1", True)]),                    # not a PDC class
        _concept("631", [("e.9", True)]),                    # not in the group, so nothing is in
    ], "left_out": []}, PDC, used)
    assert [s["candidate"]["concept"]["pdc"] for s in made] == ["631.5~2", "631.5~3"]
    assert len(problems) == 4
    first, second = made
    assert [m["in"] for m in first["candidate"]["members"]] == [True, False, True]
    assert [m["lead"] for m in first["candidate"]["members"]] == [False, False, True]
    assert [m["in"] for m in second["candidate"]["members"]] == [False, True]
    assert first["target"] == first["refs"][0]
    want = json.loads(FIXTURE.read_text())
    assert set(want) - {"id", "created", "engine"} <= set(first)
    assert set(want["candidate"]["concept"]) == set(first["candidate"]["concept"])
    assert set(want["candidate"]["members"][0]) == set(first["candidate"]["members"][0])
    blob = json.loads(sg.candidate_blob(first))
    assert blob["classifies"] == [first["refs"][0], first["refs"][2]]


def test_rejected_suggestions_free_their_extracts():
    s = {**json.loads(FIXTURE.read_text())}
    state = sg.State([s], [], set())
    assert state.covered() == {m["cid"] for m in s["candidate"]["members"] if m["in"]}
    state.reviews = [{"suggestion": s["id"], "action": "reject", "created": "1"}]
    assert state.covered() == set()
    assert "628.9~1" in state.codes()
    assert sg.next_code("628.9", state.codes()) == "628.9~2"


class FakeClient:
    """Answers each group with one concept holding its first two extracts."""

    def __init__(self):
        self.bodies = []
        self.messages = self

    def create(self, **body):
        self.bodies.append(body)
        extracts = json.loads(body["messages"][0]["content"].split("Extracts:\n", 1)[1])
        answer = {"concepts": [_concept("631", [(x["id"], i < 2) for i, x in enumerate(extracts)])],
                  "left_out": []}
        return types.SimpleNamespace(
            stop_reason="end_turn", model="claude-opus-5-5-test",
            content=[types.SimpleNamespace(type="text", text=json.dumps(answer))],
            usage=types.SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
                                        cache_read_input_tokens=0))


class FakeSigner:
    def __init__(self):
        self.made = []

    def sign(self, created, target, value):
        ident = sg.cid(sign.triage("pub:sha2-256:key", created, target, value))
        self.made.append(ident)
        return ident

    def export(self):
        return {}


def test_a_run_writes_suggestions_and_is_not_repeated(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTS_NOW", "2026-10-29T00:00:00Z")
    client, state, out = FakeClient(), tmp_path / "state", tmp_path / "out"
    state.mkdir()
    record = job.run(state, out, pdc=PDC, client=client, signer=FakeSigner(), log=lambda *_: None)
    groups = sg.groups(sg.snapshot(sg.registry(), sg.gathered()), set())
    assert len(client.bodies) == len(groups) and len(record["suggestions"]) == len(groups)
    assert not record["problems"]
    # The PDC list is the cached prefix, the same for every group.
    assert len({json.dumps(b["system"]) for b in client.bodies}) == 1
    assert client.bodies[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}
    written = [json.loads(p.read_text()) for p in (out / "suggestions").glob("*.json")]
    assert {s["id"] for s in written} == set(record["suggestions"])
    s = written[0]
    assert s["engine"]["model"] == sg.MODEL and s["engine"]["version"] == "claude-opus-5-5-test"
    for blob in (s["engine"]["prompt"], s["engine"]["inputs"], s["candidate"]["blob"]):
        assert sg.cid((out / "blobs" / sg.filename(blob)).read_bytes()) == blob
    assert json.loads((out / "blobs" / sg.filename(s["engine"]["prompt"])).read_bytes())["model"] == sg.MODEL

    # The same inputs again: nothing to do.
    again = job.run(out, tmp_path / "out2", pdc=PDC, client=client, signer=FakeSigner(), log=lambda *_: None)
    assert again["skipped"] and len(client.bodies) == len(groups)


def test_a_refused_group_is_skipped_and_the_run_not_recorded(tmp_path):
    class Refusing(FakeClient):
        def create(self, **body):
            return types.SimpleNamespace(stop_reason="refusal", stop_details=types.SimpleNamespace(category="x"))
    state = tmp_path / "state"
    state.mkdir()
    record = job.run(state, tmp_path / "out", pdc=PDC, client=Refusing(), signer=FakeSigner(),
                     log=lambda *_: None)
    assert record["problems"] and not record["suggestions"]
    assert not (tmp_path / "out" / "runs").exists()


def _pub() -> str | None:
    pub = os.environ.get("PUB") or shutil.which("pub")
    if not pub or not (Path(pub).parent / "pub-store").exists():
        return None
    return pub


@pytest.mark.skipif(_pub() is None, reason="needs pub and pub-store from publet-cli v0.2.2 or later")
def test_triage_is_signed_by_the_suggester_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PUB", _pub())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SUGGESTER_KEY", raising=False)
    for module in (sg, sign):
        monkeypatch.setattr(module, "DIR", tmp_path / "suggest")
        monkeypatch.setattr(module, "KEYS", tmp_path / "suggest" / "keys.json")
    key = sign.make_key("2026-10-29T00:00:00Z")
    assert sg.keys()[sg.MODEL]["key"] == key
    with pytest.raises(sg.SuggestError):
        sign.make_key("2026-10-29T00:00:00Z")
    signer = sign.Signer()
    try:
        made = signer.sign("2026-10-29T00:00:00Z", "pub:sha2-256:" + "a" * 52,
                           {"finding": "groups-under", "refs": [], "candidate": sg.cid(b"{}"),
                            "engine": {"model": "m", "version": "v", "prompt": sg.cid(b"p"),
                                       "inputs": sg.cid(b"i")}})
        objects = signer.export()
    finally:
        signer.close()
    assert made in objects and key in objects and len(objects) == 3  # triage, its signature, the key
    assert sg.cid(objects[made]) == made


def test_a_claude_code_session_can_draft_in_place_of_the_api(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTS_NOW", "2026-10-29T00:00:00Z")
    state, reqs = tmp_path / "state", tmp_path / "requests"
    state.mkdir()
    paths = job.requests(state, reqs, pdc=PDC)
    fake = FakeClient()
    for path in paths[1:]:  # the session answers every group but the first
        answer = fake.create(**json.loads(path.read_text())).content[0].text
        path.with_name(path.stem + ".draft.json").write_text(answer)
    record = job.run(state, tmp_path / "out", pdc=PDC, client=job.Drafts(reqs, "claude-code"),
                     signer=FakeSigner(), log=lambda *_: None)
    assert len(record["suggestions"]) == len(paths) - 1
    assert record["problems"] == [f"{paths[0].stem}: no draft for this request"]
    s = json.loads(next((tmp_path / "out" / "suggestions").glob("*.json")).read_text())
    assert s["engine"]["version"] == "claude-code"
