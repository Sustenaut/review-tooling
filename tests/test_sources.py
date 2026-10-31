"""Paper extracts: the registry's shape, what is gathered, and the check page.

On a made-up registry (fixtures/root/). The repository holding a real registry
also tests its own data.
"""

from __future__ import annotations

import copy
import http.client
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from review_tooling import sources
from review_tooling.sources import gather, review, upload


@pytest.fixture
def reg() -> dict:
    """The registry as drafted: no extract checked yet."""
    out = copy.deepcopy(sources.registry())
    for e in out["extracts"]:
        e["checked"] = None
    return out


def affirm(reg: dict, *ids: str) -> None:
    for e in reg["extracts"]:
        if e["id"] in ids:
            e["checked"] = {"by": "reader.david", "at": "2026-10-29", "finding": "affirm"}


def extract(reg: dict, ident: str) -> dict:
    return next(e for e in reg["extracts"] if e["id"] == ident)


def test_the_registry_is_well_formed(reg, tmp_path):
    assert [str(p) for p in sources.lint(reg, papers=tmp_path)] == []


def test_a_quote_over_25_words_is_refused(reg, tmp_path):
    extract(reg, "e.maple_2024.pump_energy")["quote"] = " ".join(["word"] * 24 + ["12.5"] * 2)
    assert any("quote is 1 to 25 words" in p.message for p in sources.lint(reg, papers=tmp_path))


def test_a_locator_starts_with_a_page_of_the_file(reg, tmp_path):
    extract(reg, "e.maple_2024.pump_energy")["locator"] = "sec 2.2"
    extract(reg, "e.maple_2024.pump_power")["locator"] = "p 30; sec 2.2"
    messages = [str(p) for p in sources.lint(reg, papers=tmp_path)]
    assert any("pump_energy: locator is" in m for m in messages)
    assert any("pump_power: page 30 is not in the file's 29 pages" in m for m in messages)


def test_the_value_must_be_in_its_quote(reg, tmp_path):
    extract(reg, "e.maple_2024.pump_energy")["value"] = {"value": "24.6"}
    assert any("the value is not in the quote" in p.message for p in sources.lint(reg, papers=tmp_path))


def test_a_unit_outside_its_kind_is_refused(reg, tmp_path):
    extract(reg, "e.maple_2024.pump_energy")["unit"] = "W"
    assert any("unit W is not one of" in p.message for p in sources.lint(reg, papers=tmp_path))


def test_a_quote_is_found_across_line_breaks_superscripts_and_an_ellipsis():
    page = "covered an area of 800 m² , including 700 m2 for the hydro-\nponic system and 100 m2 for walkways"
    assert sources.quote_on_page("an area of 800 m², including 700 m² for the hydroponic system", [page])
    assert sources.quote_on_page("covered an area … 100 m² for walkways", [page])
    assert not sources.quote_on_page("an area of 900 m²", [page])
    assert not sources.quote_on_page("100 m2 for walkways … covered an area", [page])


def test_status_follows_the_spec(reg):
    lead, plain = extract(reg, "e.maple_2024.yield_max"), extract(reg, "e.maple_2024.yield_mean")
    assert (sources.status(lead), sources.status(plain)) == ("lead", "to-verify")
    affirm(reg, plain["id"], lead["id"])
    assert (sources.status(lead), sources.status(plain)) == ("lead", "estimate")
    lead["derived_from"] = plain["id"]
    assert sources.status(lead) == "estimate"
    plain["checked"]["finding"] = "reject"
    assert sources.status(plain) == "rejected"


def test_nothing_is_gathered_until_a_person_checks_it(reg):
    assert gather.needed(reg) == set()
    assert gather.items(reg) == []


def test_a_checked_extract_brings_what_it_cites(reg):
    affirm(reg, "e.maple_2024.climate")
    ids = {i.id for i in gather.items(reg)}
    assert {"e.maple_2024.climate", "e.maple_2024.climate#verdict",
            "src.maple_2024", "s.north_farm", "s.north", "c.farm", "c.place",
            "k.climate_per_mass", "co2e", "ucum", "m.reported", "sc.grid",
            "extractor.claude#automated"} == ids
    signers = {i.id: gather.signer(i) for i in gather.items(reg)}
    assert signers["e.maple_2024.climate"] == "extractor.claude"
    assert signers["e.maple_2024.climate#verdict"] == "reader.david"
    assert signers["src.maple_2024"] == "reader.david"


def test_a_check_claim_waits_for_every_extract_it_compares(reg):
    check = next(c for c in reg["checks"] if c["id"] == "ch.maple_2024.pump")
    affirm(reg, *check["depends"][:-1])
    assert check["id"] not in gather.needed(reg)
    affirm(reg, check["depends"][-1])
    assert check["id"] in gather.needed(reg)


def test_a_rejected_extract_is_not_gathered(reg):
    affirm(reg, "e.maple_2024.pump_energy")
    extract(reg, "e.maple_2024.pump_energy")["checked"]["finding"] = "reject"
    assert gather.needed(reg) == set()


def test_check_reports_a_checked_extract_nobody_gathered(reg):
    affirm(reg, "e.maple_2024.pump_energy")
    problems = gather.check(reg, {"keys": {}, "items": {}, "objects": {}, "files": {}})
    assert "e.maple_2024.pump_energy: checked but never gathered" in problems


def test_check_reports_an_object_not_uploaded(reg):
    gathered = {"keys": {}, "items": {}, "objects": {"pub_sha2-256_x.cbor": None}, "files": {}}
    assert "pub_sha2-256_x.cbor: gathered but not uploaded" in gather.check(reg, gathered)


def test_only_an_open_licence_pdf_has_an_address(reg):
    source = reg["sources"][0]
    assert sources.file_url(source) == f"{sources.STORAGE}/f/{source['file_sha256']}/{source['file_name']}"
    source["licence"] = "All rights reserved"
    assert sources.file_url(source) is None


def test_the_check_page_records_a_finding_and_refuses_a_bad_edit(reg):
    ident = "e.maple_2024.pump_energy"
    assert review.apply(reg, ident, "affirm", today="2026-10-29") == []
    assert extract(reg, ident)["checked"] == {"by": "reader.david", "at": "2026-10-29", "finding": "affirm"}
    problems = review.apply(reg, ident, "affirm", {"value": "99"}, today="2026-10-30")
    assert problems and extract(reg, ident)["value"] == {"value": "12.5"}
    assert extract(reg, ident)["checked"]["at"] == "2026-10-29"
    assert review.apply(reg, ident, None) == [] and extract(reg, ident)["checked"] is None


def test_an_edited_unit_moves_the_extract_to_the_kind_that_lists_it(reg):
    ident = "e.maple_2024.water_use"
    extract(reg, ident).update(unit="L", kind="k.volume")
    assert review.apply(reg, ident, "affirm", {"unit": "L/d"}, today="2026-10-29") == []
    assert (extract(reg, ident)["unit"], extract(reg, ident)["kind"]) == ("L/d", "k.volume_per_day")


def test_the_check_page_lists_every_extract_with_its_page(reg):
    rows = review.rows(reg)
    assert len(rows) == len(reg["extracts"])
    row = next(r for r in rows if r["id"] == "e.maple_2024.pump_energy")
    assert (row["page"], row["status"], row["short"]) == (10, "to-verify", "Maple and Oak (2024)")


def test_fetch_names_itself_because_cloudflare_refuses_pythons_default(tmp_path, monkeypatch):
    """Storage is behind Cloudflare, which answers Python's own User-Agent with error 1010."""
    import io
    import urllib.request

    asked = []

    def urlopen(request):
        asked.append(request)
        return io.BytesIO(b"content")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("SOURCES_TOKEN", "a-token")
    gather.fetch({"objects": {"a.cbor": "https://storage.example/a.cbor"}}, tmp_path)
    assert (tmp_path / "a.cbor").read_bytes() == b"content"
    assert asked[0].get_header("User-agent") == "sustenaut-sources"
    assert asked[0].get_header("Authorization") == "Bearer a-token"


# --- the check page: only this page may use it --------------------------------

@pytest.fixture
def page(reg, tmp_path, monkeypatch):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(reg))
    (tmp_path / ("a" * 64 + ".pdf")).write_bytes(b"%PDF")
    monkeypatch.setattr(review.Handler, "papers", tmp_path, raising=False)
    monkeypatch.setattr(review.Handler, "registry_path", path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), review.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], path
    server.shutdown()
    server.server_close()


def ask(port, method, target, body=None, **headers):
    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
    data = json.dumps(body).encode() if body is not None else b""
    for name, value in {"Host": f"127.0.0.1:{port}", "Content-Length": str(len(data)), **headers}.items():
        connection.putheader(name.replace("_", "-"), value)
    connection.endheaders(data)
    reply = connection.getresponse()
    return reply.status, reply.read()


FINDING = {"id": "e.maple_2024.pump_energy", "finding": "affirm"}


def test_the_page_records_a_finding_from_itself(page):
    port, path = page
    status, _ = ask(port, "POST", "/api/check", FINDING, Origin=f"http://127.0.0.1:{port}", Content_Type="application/json")
    assert status == 200 and extract(json.loads(path.read_text()), FINDING["id"])["checked"]["finding"] == "affirm"
    status, _ = ask(port, "POST", "/api/check", FINDING, Host=f"localhost:{port}", Origin=f"http://localhost:{port}",
                    Content_Type="application/json")
    assert status == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example", "Content_Type": "application/json"},
    {"Content_Type": "application/json"},  # no Origin at all
    {"Origin": "http://127.0.0.1:1", "Content_Type": "application/json"},  # another local port
    {"Origin": "PAGE", "Content_Type": "text/plain"},  # what a form may send without asking
    {"Origin": "PAGE"},
    {"Origin": "PAGE", "Content_Type": "application/json", "Host": "evil.example"},  # DNS rebinding
])
def test_a_finding_from_anywhere_else_changes_nothing(page, headers):
    port, path = page
    before = path.read_text()
    filled = {k: f"http://127.0.0.1:{port}" if v == "PAGE" else v for k, v in headers.items()}
    status, _ = ask(port, "POST", "/api/check", FINDING, **filled)
    assert status == 403 and path.read_text() == before


def test_another_host_reads_nothing(page):
    port, _ = page
    assert ask(port, "GET", "/api/extracts", Host="evil.example")[0] == 403
    assert ask(port, "GET", "/", Host="evil.example")[0] == 403
    assert ask(port, "GET", "/api/extracts")[0] == 200


def test_a_pdf_is_served_by_its_sha_and_a_miss_names_no_folder(page, tmp_path):
    port, _ = page
    assert ask(port, "GET", f"/pdf/{'a' * 64}.pdf") == (200, b"%PDF")
    status, body = ask(port, "GET", "/pdf/../../secret.pdf")
    assert status == 404 and str(tmp_path).encode() not in body and b"secret" not in body


def test_upload_refuses_a_sha_that_is_not_one_before_reading_anything(reg, tmp_path, monkeypatch):
    reg["sources"][0]["file_sha256"] = "../../secret"
    (tmp_path.parent / "secret.pdf").write_bytes(b"not to be read")
    monkeypatch.setattr(sources, "registry", lambda *_: reg)
    monkeypatch.setattr(sources, "token", lambda: "t")
    monkeypatch.setattr(gather, "load_gathered", lambda *_: {
        "keys": {}, "objects": {}, "files": {}, "items": {reg["sources"][0]["id"]: {"cid": "x"}}})
    monkeypatch.setattr(upload, "post", lambda *_: pytest.fail("nothing is uploaded"))
    with pytest.raises(SystemExit, match="not 64 hex"):
        upload.upload(tmp_path)
