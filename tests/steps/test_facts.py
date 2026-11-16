"""The facts registry: its shape, and that the plan's prose agrees with it.

On a made-up registry (fixtures/root/). The repository holding a real registry
also tests its own data.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

from review_tooling import facts
from pytest_bdd import scenarios, then

scenarios("facts.feature")


@then("every local id the registry names is defined")
def every_local_id_the_registry_names_is_defined():
    reg = facts.registry()
    ids = {item["id"] for part in ("definitions", "procedures", "singulars") for item in reg[part]}
    for item in reg["definitions"] + reg["singulars"]:
        for d in item.get("depends", []):
            assert d in ids, f"{item['id']} depends on {d}, which the registry does not define"
    for s in reg["singulars"]:
        assert s["class"] in ids and s.get("part_of", s["class"]) in ids, s["id"]
    for f in reg["facts"]:
        for field in ("about", "concept", "method"):
            assert f[field] in ids, f"{f['id']}: {field} {f[field]} is not defined"
        for name in f.get("statements", []) + ([f["measure"]] if f.get("measure") else []):
            assert name in ids, f"{f['id']}: {name} is not defined"


@then("a singular is located by coordinates never by a typed cell")
def a_singular_is_located_by_coordinates_never_by_a_typed_cell():
    for s in facts.registry()["singulars"]:
        location = s.get("location")
        assert location is None or set(location) <= {"lat", "lng", "resolution"}, s["id"]


@then("each lint allowance still matches a line")
def each_lint_allowance_still_matches_a_line():
    reg = facts.registry()
    for a in reg["lint_allow"]:
        assert a["text"] in (facts.ROOT / a["file"]).read_text(), f"stale allowance: {a}"


@then("a disagreeing area beside its room is reported")
def a_disagreeing_area_beside_its_room_is_reported(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "note.md").write_text("The Harbour cellar is 25 m².\nThe Widget part is €7 in bulk.\n")
    found = facts.lint(facts.registry(), root=tmp_path, dirs=("plans",))
    assert [(d.line, d.text, d.fact.fact) for d in found] == [
        (1, "25 m²", "f.harbour.cellar.floor_area"),
        (2, "€7", "f.widget.part_cost"),
    ]


@then("an agreeing figure and an allowed quote pass")
def an_agreeing_figure_and_an_allowed_quote_pass(tmp_path):
    reg = facts.registry()
    reg["lint_allow"] = [{"file": "plans/note.md", "text": "15 m²", "why": "quotes the old figure"}]
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "note.md").write_text("The Harbour cellar, once noted as 15 m², is 20 m2.\n")
    assert facts.lint(reg, root=tmp_path, dirs=("plans",)) == []


# --- gathering --------------------------------------------------------------

from review_tooling.facts import gather  # noqa: E402


def _recorded(reg: dict) -> dict:
    """A record that agrees with `reg`, as a gather would leave it, without pub:
    each item's object is made up, its bytes the item's id."""
    items, cids = {}, {}
    for item in gather.items(reg):
        cids[item.id] = gather.cid_of(item.id.encode())
        items[item.id] = {"cid": cids[item.id], "digest": item.digest(cids)}
    return {"items": items, "objects": sorted(gather.filename(c) for c in cids.values())}


def _edited(fact_id: str, **changes) -> dict:
    reg = facts.registry()
    next(f for f in reg["facts"] if f["id"] == fact_id).update(changes)
    return reg


@then("an unchanged registry composes nothing")
def an_unchanged_registry_composes_nothing():
    reg = facts.registry()
    p = gather.plan(gather.items(reg), _recorded(reg))
    assert (p.compose, p.revise, p.retract) == ([], [], [])


@then("an edited value revises that fact alone")
def an_edited_value_revises_that_fact_alone():
    reg = _edited("f.widget.part_cost", value="4")
    p = gather.plan(gather.items(reg), _recorded(facts.registry()))
    assert (p.compose, p.revise, p.retract) == ([], ["f.widget.part_cost"], [])


@then("a revised definition revises what cites it")
def a_revised_definition_revises_what_cites_it():
    reg = facts.registry()
    next(d for d in reg["definitions"] if d["id"] == "q.floor_area")["content"] += " Inside the walls."
    p = gather.plan(gather.items(reg), _recorded(facts.registry()))
    assert p.revise == ["q.floor_area", "f.harbour.cellar.floor_area",
                        "f.harbour.floor_area", "f.harbour.floor_area#open0"]


@then("check reports a registry edit nobody gathered")
def check_reports_a_registry_edit_nobody_gathered(tmp_path):
    reg = _edited("f.harbour.cellar.floor_area", value="21")
    assert gather.check(reg, _recorded(facts.registry()), tmp_path / "none") == [
        "f.harbour.cellar.floor_area: changed in the registry since it was gathered"]


@then("check reports a fact removed without retraction")
def check_reports_a_fact_removed_without_retraction(tmp_path):
    reg = facts.registry()
    reg["facts"] = [f for f in reg["facts"] if f["id"] != "f.widget.part_cost"]
    assert gather.check(reg, _recorded(facts.registry()), tmp_path / "none") == [
        "f.widget.part_cost: gathered but gone from the registry, and not retracted"]


@then("check reports a stray object and tampered bytes")
def check_reports_a_stray_object_and_tampered_bytes(tmp_path):
    reg = facts.registry()
    record = _recorded(reg)
    objects = tmp_path / "objects"
    objects.mkdir()
    for item_id, entry in record["items"].items():
        (objects / gather.filename(entry["cid"])).write_bytes(item_id.encode())
    assert gather.check(reg, record, objects) == []
    stray = objects / gather.filename(gather.cid_of(b"stray"))
    stray.write_bytes(b"stray")
    victim = objects / record["objects"][0]
    victim.write_bytes(b"not what was signed")
    assert gather.check(reg, record, objects) == [
        f"{stray.name}: in corpus/objects but not recorded by any gather",
        f"{victim.name}: its bytes do not hash to its name"]


@then("a registry nothing was gathered from composes all of it")
def a_registry_nothing_was_gathered_from_composes_all_of_it():
    items = gather.items(facts.registry())
    p = gather.plan(items, {"items": {}})
    assert set(p.compose) == {i.id for i in items} and p.revise == p.retract == []


@then("inputs do not depend on the pub that composes them")
def inputs_do_not_depend_on_the_pub_that_composes_them(monkeypatch):
    before = [i.id for i in gather.items(facts.registry())]
    monkeypatch.setenv("PUB", "/nonexistent/pub")
    assert [i.id for i in gather.items(facts.registry())] == before


# --- storage: the objects live in a private tenant, not in git -----------------

def _gathered(*data: bytes) -> dict:
    return {"objects": [gather.filename(gather.cid_of(d)) for d in data], "items": {}}


@then("an objects address is the sha256 its name holds")
def an_objects_address_is_the_sha256_its_name_holds():
    data = b"an object"
    name = gather.filename(gather.cid_of(data))
    assert gather.storage_id(name) == hashlib.sha256(data).hexdigest()


@then("a record that is not an objects name reaches nothing")
def a_record_that_is_not_an_objects_name_reaches_nothing(tmp_path, monkeypatch):
    """Every case of the parametrized test this was, in turn."""
    for name in ["../../secret.cbor", "pub_sha2-256_short.cbor", "a.pdf",
                                  "pub_sha2-256_" + "a" * 52 + ".cbor/../x"]:
        name = name.values[0] if type(name).__name__ == "ParameterSet" else name
        case_dir = tmp_path / f"case-{len(list(tmp_path.iterdir()))}"
        case_dir.mkdir()
        _case_a_record_that_is_not_an_objects_name_reaches_nothing(name=name, tmp_path=case_dir, monkeypatch=monkeypatch)


def _case_a_record_that_is_not_an_objects_name_reaches_nothing(name, tmp_path, monkeypatch):
    asked = _storage(monkeypatch, {})
    with pytest.raises(gather.GatherError, match="is not an object's name"):
        gather.fetch({"objects": [name], "items": {}}, tmp_path)
    with pytest.raises(gather.GatherError, match="is not an object's name"):
        gather.upload({"objects": [name], "items": {}}, tmp_path)
    assert asked == []


def _storage(monkeypatch, holding: dict[str, bytes]) -> list:
    asked = []

    def urlopen(request):
        asked.append(request)
        return io.BytesIO(holding[request.full_url.rsplit("/", 1)[1]])

    monkeypatch.setenv("SOURCES_TOKEN", "a-token")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return asked


@then("fetch takes what is missing with a token and checks each")
def fetch_takes_what_is_missing_with_a_token_and_checks_each(tmp_path, monkeypatch):
    kept, missing = b"kept", b"missing"
    (tmp_path / gather.filename(gather.cid_of(kept))).write_bytes(kept)
    name = gather.filename(gather.cid_of(missing))
    asked = _storage(monkeypatch, {name: missing})
    gather.fetch(_gathered(kept, missing), tmp_path)
    assert (tmp_path / name).read_bytes() == missing and len(asked) == 1
    assert asked[0].full_url == f"{facts.STORAGE}/f/{gather.storage_id(name)}/{name}"
    assert asked[0].get_header("Authorization") == "Bearer a-token"


@then("fetch says what it is fetching and nothing when all is here")
def fetch_says_what_it_is_fetching_and_nothing_when_all_is_here(tmp_path, monkeypatch, capsys):
    name = gather.filename(gather.cid_of(b"one"))
    _storage(monkeypatch, {name: b"one"})
    gather.fetch(_gathered(b"one"), tmp_path)
    assert "fetching objects from storage: 1" in capsys.readouterr().err
    gather.fetch(_gathered(b"one"), tmp_path)
    assert capsys.readouterr().err == ""


@then("fetch refuses bytes that do not hash to the name")
def fetch_refuses_bytes_that_do_not_hash_to_the_name(tmp_path, monkeypatch):
    name = gather.filename(gather.cid_of(b"signed"))
    _storage(monkeypatch, {name: b"changed"})
    with pytest.raises(gather.GatherError, match="does not hash"):
        gather.fetch(_gathered(b"signed"), tmp_path)
    assert not (tmp_path / name).exists()


@then("upload sends each object and refuses another address")
def upload_sends_each_object_and_refuses_another_address(tmp_path, monkeypatch):
    from review_tooling.sources import upload as sources_upload

    (tmp_path / gather.filename(gather.cid_of(b"one"))).write_bytes(b"one")
    sent = []

    def post(name, data, label, auth, endpoint):
        sent.append((name, data, auth, endpoint))
        return {"id": gather.storage_id(name)}

    monkeypatch.setenv("SOURCES_TOKEN", "a-token")
    monkeypatch.setattr(sources_upload, "post", post)
    assert gather.upload(_gathered(b"one"), tmp_path) == 1
    assert sent[0][1:] == (b"one", "a-token", f"{facts.STORAGE}/files")
    monkeypatch.setattr(sources_upload, "post", lambda *_: {"id": "other"})
    with pytest.raises(gather.GatherError, match="storage named it"):
        gather.upload(_gathered(b"one"), tmp_path)


@then("check without a local copy compares only the registry and the record")
def check_without_a_local_copy_compares_only_the_registry_and_the_record(tmp_path):
    reg = facts.registry()
    items = gather.items(reg)
    assert gather.check(reg, _recorded(reg), tmp_path / "no") == []
    assert any("never gathered" in p for p in gather.check(reg, {"items": {}, "objects": []}, tmp_path / "no"))
    recorded = {"items": {"x": {"cid": gather.cid_of(b"x"), "input": "y"}}, "objects": []}
    assert any("is not recorded" in p for p in gather.check(reg, recorded, tmp_path / "no"))
    assert items


# --- gathering with pub: what is published is kept, changes become new versions

# These drive a real pub (and pub-store beside it) in a scratch corpus with a
# key made for the test. CI has no pub, so they run where one is: set PUB, or
# put pub and pub-store on PATH.
def _pub() -> str | None:
    pub = os.environ.get("PUB") or shutil.which("pub")
    if not pub or not Path(gather.store_binary(pub)).exists():
        return None
    return pub


def _needs_pub() -> str:
    pub = _pub()
    if pub is None:
        pytest.skip("needs pub and pub-store: set PUB or put them on PATH")
    return pub


# The paths pub reads the made-up facts by: class, singulars, concept.
PATH_CELLAR_AREA = "place.harbour.cellar.floor_area"
PATH_HARBOUR_AREA = "place.harbour.floor_area"


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A scratch corpus holding the made-up registry, and a fresh key."""
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(facts.registry()))
    monkeypatch.setenv("FACTS_KEY", str(tmp_path / "secret.key"))
    monkeypatch.setenv("FACTS_NOW", "2026-11-01T00:00:00Z")
    for name, value in [("OBJECTS", tmp_path / "objects"), ("GATHERED", tmp_path / "gathered.json"),
                        ("KEY_ID", tmp_path / "key")]:
        monkeypatch.setattr(gather, name, value)
    (tmp_path / "objects").mkdir()
    return path


def _files(corpus: Path) -> set[str]:
    return {p.name for p in (corpus.parent / "objects").glob("*.cbor")}


def _get(corpus: Path, path: str) -> str:
    return subprocess.run([os.environ["PUB"], "get", path, f"--dir={corpus.parent / 'objects'}"],
                          capture_output=True, text=True).stdout


def _edit(corpus: Path, fact_id: str, **changes) -> None:
    reg = json.loads(corpus.read_text())
    next(f for f in reg["facts"] if f["id"] == fact_id).update(changes)
    corpus.write_text(json.dumps(reg))


@then("gathering an unchanged registry again adds nothing")
def gathering_an_unchanged_registry_again_adds_nothing(corpus, monkeypatch):
    monkeypatch.setenv("PUB", _needs_pub())
    gather.gather(corpus)
    before = _files(corpus)

    def refuse(*_):
        raise AssertionError("an unchanged item was composed again")

    monkeypatch.setattr(gather, "compose", refuse)
    p = gather.gather(corpus)
    assert (p.compose, p.revise) == ([], []) and _files(corpus) == before


@then("an edited value is a new version and the old object stays")
def an_edited_value_is_a_new_version_and_the_old_object_stays(corpus, monkeypatch):
    monkeypatch.setenv("PUB", _needs_pub())
    gather.gather(corpus)
    old_cid = gather.load_gathered(gather.GATHERED)["items"]["f.harbour.cellar.floor_area"]["cid"]
    before = _files(corpus)
    _edit(corpus, "f.harbour.cellar.floor_area", value="21")

    p = gather.gather(corpus)

    assert p.revise == ["f.harbour.cellar.floor_area"]
    assert before < _files(corpus) and gather.filename(old_cid) in _files(corpus)
    shown = _get(corpus, PATH_CELLAR_AREA)
    assert "value     value 21" in shown and "value 20" not in shown
    assert gather.check(json.loads(corpus.read_text()), gather.load_gathered(gather.GATHERED)) == []


@then("a removed open note is retracted not deleted")
def a_removed_open_note_is_retracted_not_deleted(corpus, monkeypatch):
    monkeypatch.setenv("PUB", _needs_pub())
    gather.gather(corpus)
    note = gather.load_gathered(gather.GATHERED)["items"]["f.harbour.floor_area#open0"]["cid"]
    _edit(corpus, "f.harbour.floor_area", open=[])

    p = gather.gather(corpus)

    assert p.retract == ["f.harbour.floor_area#open0"]
    assert gather.filename(note) in _files(corpus)
    assert gather.load_gathered(gather.GATHERED)["items"]["f.harbour.floor_area#open0"]["retracted"]
    # With the note retracted, nothing leaves the fact open: a value stated
    # without a measure reads as an estimate (publet#65, spec 5.9.7).
    assert "status    estimate" in _get(corpus, PATH_HARBOUR_AREA)
