"""The facts registry: its shape, and that the plan's prose agrees with it.

On a made-up registry (fixtures/root/). The repository holding a real registry
also tests its own data.
"""

from __future__ import annotations

import hashlib
import io
import urllib.request

import pytest

from review_tooling import facts


def test_every_local_id_the_registry_names_is_defined():
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


def test_a_singular_is_located_by_coordinates_never_by_a_typed_cell():
    for s in facts.registry()["singulars"]:
        location = s.get("location")
        assert location is None or set(location) <= {"lat", "lng", "resolution"}, s["id"]


def test_each_lint_allowance_still_matches_a_line():
    reg = facts.registry()
    for a in reg["lint_allow"]:
        assert a["text"] in (facts.ROOT / a["file"]).read_text(), f"stale allowance: {a}"


def test_a_disagreeing_area_beside_its_room_is_reported(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "note.md").write_text("The Harbour cellar is 25 m².\nThe Widget part is €7 in bulk.\n")
    found = facts.lint(facts.registry(), root=tmp_path, dirs=("plans",))
    assert [(d.line, d.text, d.fact.fact) for d in found] == [
        (1, "25 m²", "f.harbour.cellar.floor_area"),
        (2, "€7", "f.widget.part_cost"),
    ]


def test_an_agreeing_figure_and_an_allowed_quote_pass(tmp_path):
    reg = facts.registry()
    reg["lint_allow"] = [{"file": "plans/note.md", "text": "15 m²", "why": "quotes the old figure"}]
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "note.md").write_text("The Harbour cellar, once noted as 15 m², is 20 m2.\n")
    assert facts.lint(reg, root=tmp_path, dirs=("plans",)) == []


# --- gathering --------------------------------------------------------------

from review_tooling.facts import gather  # noqa: E402


def test_a_registry_nothing_was_gathered_from_composes_all_of_it():
    items = gather.items(facts.registry())
    p = gather.plan(items, {"items": {}})
    assert set(p.compose) == {i.id for i in items} and p.revise == p.retract == []


def test_inputs_do_not_depend_on_the_pub_that_composes_them(monkeypatch):
    before = [i.id for i in gather.items(facts.registry())]
    monkeypatch.setenv("PUB", "/nonexistent/pub")
    assert [i.id for i in gather.items(facts.registry())] == before


# --- storage: the objects live in a private tenant, not in git -----------------

def _gathered(*data: bytes) -> dict:
    return {"objects": [gather.filename(gather.cid_of(d)) for d in data], "items": {}}


def test_an_objects_address_is_the_sha256_its_name_holds():
    data = b"an object"
    name = gather.filename(gather.cid_of(data))
    assert gather.storage_id(name) == hashlib.sha256(data).hexdigest()


def _storage(monkeypatch, holding: dict[str, bytes]) -> list:
    asked = []

    def urlopen(request):
        asked.append(request)
        return io.BytesIO(holding[request.full_url.rsplit("/", 1)[1]])

    monkeypatch.setenv("SOURCES_TOKEN", "a-token")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return asked


def test_fetch_takes_what_is_missing_with_a_token_and_checks_each(tmp_path, monkeypatch):
    kept, missing = b"kept", b"missing"
    (tmp_path / gather.filename(gather.cid_of(kept))).write_bytes(kept)
    name = gather.filename(gather.cid_of(missing))
    asked = _storage(monkeypatch, {name: missing})
    gather.fetch(_gathered(kept, missing), tmp_path)
    assert (tmp_path / name).read_bytes() == missing and len(asked) == 1
    assert asked[0].full_url == f"{facts.STORAGE}/f/{gather.storage_id(name)}/{name}"
    assert asked[0].get_header("Authorization") == "Bearer a-token"


def test_fetch_refuses_bytes_that_do_not_hash_to_the_name(tmp_path, monkeypatch):
    name = gather.filename(gather.cid_of(b"signed"))
    _storage(monkeypatch, {name: b"changed"})
    with pytest.raises(gather.GatherError, match="does not hash"):
        gather.fetch(_gathered(b"signed"), tmp_path)
    assert not (tmp_path / name).exists()


def test_upload_sends_each_object_and_refuses_another_address(tmp_path, monkeypatch):
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


def test_check_without_a_local_copy_compares_only_the_registry_and_the_record(tmp_path):
    reg = facts.registry()
    items = gather.items(reg)
    assert any("never gathered" in p for p in gather.check(reg, {"items": {}, "objects": []}, tmp_path / "no"))
    recorded = {"items": {"x": {"cid": gather.cid_of(b"x"), "input": "y"}}, "objects": []}
    assert any("is not recorded" in p for p in gather.check(reg, recorded, tmp_path / "no"))
    assert items
