"""The facts registry: its shape, and that the plan's prose agrees with it.

On a made-up registry (fixtures/root/). The repository holding a real registry
also tests its own data.
"""

from __future__ import annotations

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
