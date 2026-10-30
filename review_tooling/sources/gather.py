"""Compose and sign the checked extracts, with what they need, by the facts gather's rules.

Run on the laptop that holds the keys, after `python -m review_tooling.sources review`:

    make sources-gather            # or: python -m review_tooling.sources gather
    PUB=path/to/pub make sources-gather

Only what a person has checked is gathered: each extract whose `checked`
finding is `affirm`, and what it needs -- its source record, the system it
is about and the systems that one is part of, its kind, measure and
scenario, and the definitions those cite -- plus each check claim whose
extracts are all gathered, a provenance verdict per extract, and the
reader's attestation that the extractor key is automated. A draft nobody
has checked is not an object yet (R14).

Two keys sign, as corpus/sources/keys.json says. `extractor.claude`
(principal automated) signs what the model read: extracts and check
claims. `reader.david` (principal human) signs source records, systems,
scenarios, definitions, and the verdicts. Each secret is read from the path
keys.json names (overridable with $SOURCES_KEYS_DIR) and never enters the
repository; the first gather makes a key that does not exist yet.

The objects are not committed. A gather writes them to the local cache
(corpus/sources/objects/, ignored by git) and records each in
corpus/sources/gathered.json; `python -m review_tooling.sources upload` puts them on
storage.sustenaut.eu and records the address. What the facts gather keeps,
this keeps (review_tooling/facts/gather.py): an unchanged input keeps its object, a
changed one is composed again and supersedes it, and one gone from the
registry is retracted. A later gather holds every recorded object, from the
cache or else from storage.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

from review_tooling import sources
from review_tooling.facts import gather as fg
from review_tooling.facts.gather import GatherError, Item, Plan, Workspace, filename, pub_binary

OBJECTS = sources.DIR / "objects"
GATHERED = sources.GATHERED
KEYS = sources.KEYS
PURPOSE = "authored"
NAME = "The sources registry"


# --- what is gathered: pure, and the same in `check` -------------------------


def needed(reg: dict) -> set[str]:
    """The ids gathered: checked extracts and everything they cite."""
    defs = {d["id"]: d for d in reg["definitions"]}
    systems = {s["id"]: s for s in reg["systems"]}
    out: set[str] = set()

    def need(ref: str | None) -> None:
        if not ref or ref in out:
            return
        out.add(ref)
        if ref in defs:
            for r in defs[ref].get("depends", []):
                need(r)
        if ref in systems:
            need(systems[ref]["class"])
            need(systems[ref].get("part_of"))

    for e in reg["extracts"]:
        if (e.get("checked") or {}).get("finding") == "affirm":
            out.add(e["id"])
            for ref in (e["source"], e["about"], e["kind"], e["measure"], e.get("scenario")):
                need(ref)
    for c in reg["checks"]:
        if c["depends"] and all(d in out for d in c["depends"]):
            out.add(c["id"])
    return out


def signer(item: Item) -> str:
    return "extractor.claude" if item.kind in ("extract", "check") else "reader.david"


def content(e: dict, reg: dict) -> str:
    source = next(s for s in reg["sources"] if s["id"] == e["source"])
    v = e["value"]
    shown = (f"{v['low']} to {v['high']}" if "low" in v else f"at most {v['high']}" if set(v) == {"high"}
             else " ± ".join(v[k] for k in ("value", "half_width") if k in v))
    about = next((s["name"] for s in reg["systems"] if s["id"] == e["about"]), None)
    return f"{source['short']} report the {e['quantity']}{f' of {about}' if about else ''} as {shown} {e['unit']}."


def items(reg: dict) -> list[Item]:
    """Every item gathered, each after what it cites."""
    keep = needed(reg)
    pdc = {d["id"]: d["defines"]["pdc"] for d in reg["definitions"] if d["defines"]["sort"] == "concept"}
    ids = {d["id"] for d in reg["definitions"]}
    out: list[Item] = []
    for d in reg["definitions"]:
        if d["id"] in keep:
            deps = d.get("depends", [])
            out.append(Item(d["id"], "define", {"content": d["content"], "defines": d["defines"], "depends": deps},
                            deps + [v for v in json.dumps(d["defines"]).split('"') if v in ids]))
    for s in reg["systems"]:
        if s["id"] in keep:
            defines = {"sort": "singular", "name": s["name"], "class": pdc[s["class"]]}
            refs = [s["class"]]
            if s.get("part_of"):
                defines["part_of"] = s["part_of"]
                refs.append(s["part_of"])
            if s.get("location"):
                defines["location"] = dict(s["location"])
            out.append(Item(s["id"], "define", {"content": s["content"], "defines": defines,
                                                "depends": [s["class"]]}, refs))
    for s in reg["scenarios"]:
        if s["id"] in keep:
            out.append(Item(s["id"], "define", {"content": s["content"], "defines": None, "depends": []}))
    for s in reg["sources"]:
        if s["id"] in keep:
            row = [s.get("doi"), s.get("url"), s["citation"], s["version"], s["source_type"], s["licence"],
                   s["file_sha256"], sources.file_url(s)]
            names = ["doi", "url", "citation", "version", "source type", "licence", "file_sha256", "file"]
            out.append(Item(s["id"], "record", {
                "content": f"Source record: {s['citation']}",
                "scope": f"the copy read: {s['version']}, SHA-256 {s['file_sha256']}",
                "ref": f"https://doi.org/{s['doi']}" if s.get("doi") else s["url"], "revision": s["version"],
                "data": {"columns": [{"name": n} for n in names], "rows": [row]}}))
    for e in reg["extracts"]:
        if e["id"] not in keep:
            continue
        column = {"name": e["quantity"], "unit": e["unit"], "kind": e["kind"], "measure": e["measure"]}
        refs = [e["source"], e["kind"], e["measure"], "ucum"]
        if "{CO2e}" in e["unit"]:
            column["unit_defs"] = {"{CO2e}": "co2e"}
            refs.append("co2e")
        columns, row = [{"name": "about"}, column], [e["about"], dict(e["value"])]
        if e.get("cites"):
            columns.append({"name": "cites"})
            row.append(e["cites"])
        depends = ["ucum", *([e["about"]] if e["about"] else []), *([e["scenario"]] if e.get("scenario") else [])]
        refs += depends
        out.append(Item(e["id"], "extract", {
            "content": content(e, reg), "scope": e["scope"], "depends": depends, "source": e["source"],
            "locator": e["locator"], "note": e["quote"], "data": {"columns": columns, "rows": [row]}}, refs))
    for c in reg["checks"]:
        if c["id"] in keep:
            source = next(s for s in reg["sources"] if s["id"] == c["source"])
            out.append(Item(c["id"], "check", {"content": c["content"], "depends": c["depends"],
                                               "scope": f"the figures as printed in {source['short']}"}, c["depends"]))
    for e in reg["extracts"]:
        if e["id"] in keep:
            sha = next(s["file_sha256"] for s in reg["sources"] if s["id"] == e["source"])
            out.append(Item(f"{e['id']}#verdict", "verdict", {
                "target": e["id"], "by": e["checked"]["by"],
                "scope": f"read against page {sources.page(e)} of the file with SHA-256 {sha}"}, [e["id"]]))
    if keep:
        out.append(Item("extractor.claude#automated", "attests", {"key": "key:extractor.claude"},
                        ["key:extractor.claude"]))
    return out


def load_gathered(path: Path | None = None) -> dict:
    path = GATHERED if path is None else path
    if path.exists():
        return json.loads(path.read_text())
    return {"keys": {}, "items": {}, "objects": {}, "files": {}}


def known_cids(gathered: dict) -> dict[str, str]:
    cids = {f"key:{k}": v for k, v in gathered["keys"].items()}
    cids |= {k: v["cid"] for k, v in gathered["items"].items()}
    return cids


def plan(its: list[Item], gathered: dict) -> Plan:
    """What a gather would do, decided from inputs alone, as the facts gather decides it."""
    recorded = gathered["items"]
    cids = known_cids(gathered)
    p = Plan([], [], [], [])
    for item in its:
        before = recorded.get(item.id)
        if before and before["digest"] == item.digest(cids):
            p.keep.append(item.id)
            continue
        (p.revise if before else p.compose).append(item.id)
        cids[item.id] = f"pending:{item.id}"
    present = {i.id for i in its}
    p.retract = [k for k, v in recorded.items() if k not in present and not v.get("retracted")]
    return p


def check(reg: dict | None = None, gathered: dict | None = None) -> list[str]:
    """What a gather or an upload would change. Empty means the registry,
    gathered.json and storage agree; drafts nobody checked are not problems."""
    reg = sources.registry() if reg is None else reg
    gathered = load_gathered() if gathered is None else gathered
    recorded = gathered["items"]
    p = plan(items(reg), gathered)
    problems = [f"{i}: checked but never gathered" for i in p.compose]
    problems += [f"{i}: changed in the registry since it was gathered" for i in p.revise]
    problems += [f"{i}: gathered but no longer checked or present, and not retracted" for i in p.retract]
    problems += [f"{i}: its object {e['cid']} is not recorded" for i, e in recorded.items()
                 if filename(e["cid"]) not in gathered["objects"]]
    problems += [f"{name}: gathered but not uploaded" for name, url in sorted(gathered["objects"].items()) if not url]
    for s in reg["sources"]:
        if s["id"] in recorded and sources.is_open(s) and not gathered["files"].get(s["id"]):
            problems.append(f"{s['id']}: open-licence PDF not uploaded")
    return problems


# --- composing: needs pub and the keys ----------------------------------------


def keys() -> dict:
    return {k: v for k, v in json.loads(KEYS.read_text()).items() if isinstance(v, dict)}


def secret_path(name: str) -> Path:
    path = Path(os.path.expanduser(keys()[name]["secret"]))
    override = os.environ.get("SOURCES_KEYS_DIR")
    return Path(override) / path.name if override else path


def use_keys(ws: Workspace, gathered: dict, created: str) -> dict[str, str]:
    """Each key's secret, making a key that does not exist yet."""
    ws.run("init")
    secrets = {}
    for name, spec in keys().items():
        path = secret_path(name)
        if name in gathered["keys"] and path.exists():
            secrets[name] = path.read_text()
            continue
        if name in gathered["keys"] or path.exists():
            raise GatherError(f"{path} and the key recorded for {name} must both exist or neither")
        (ws.root / ".publet" / "signing.key").unlink(missing_ok=True)
        out = ws.run("sign", "--generate-key", f"--principal={spec['principal']}", f"--label={spec['label']}",
                     f"--created={created}")
        gathered["keys"][name] = next(line.split()[1] for line in out.splitlines() if line.startswith("key "))
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ws.root / ".publet" / "signing.key", path)
        path.chmod(0o600)
        secrets[name] = path.read_text()
        print(f"made the {name} key {gathered['keys'][name]}; its secret is in {path}")
    return secrets


def fetch(gathered: dict, into: Path) -> None:
    """Every recorded object not in the cache, from storage, whose `sources` tenant is private."""
    into.mkdir(parents=True, exist_ok=True)
    auth = None
    for name, url in gathered["objects"].items():
        target = into / name
        if not target.exists():
            if not url:
                raise GatherError(f"{name} is neither in {into} nor uploaded")
            # Named, as upload.py does: Cloudflare refuses Python's default User-Agent (error 1010).
            auth = auth or sources.token()
            request = urllib.request.Request(url, headers={"User-Agent": "sustenaut-sources",
                                                           "Authorization": f"Bearer {auth}"})
            with urllib.request.urlopen(request) as r:  # noqa: S310 (our own storage)
                target.write_bytes(r.read())


def compose(ws: Workspace, item: Item, cids: dict[str, str], created: str) -> str:
    x = item.input(cids)
    at = f"--created={created}"
    if x["kind"] == "define" and x["defines"] is None:
        args = ["compose", at, "--class=definitional", f"--content={x['content']}", "--scope=unconditional"]
    elif x["kind"] == "define":
        location = x["defines"].get("location")
        if location and "lat" in location:
            import h3

            x["defines"]["location"] = {"h3": h3.latlng_to_cell(location["lat"], location["lng"],
                                                                location["resolution"])}
        args = ["compose", at, "--class=definitional", f"--content={x['content']}", "--scope=unconditional",
                f"--defines={ws.file(item.id + '.json', x['defines'])}", *[f"--depends={d}" for d in x["depends"]]]
    elif x["kind"] == "record":
        args = ["compose", at, "--class=archival", f"--content={x['content']}", f"--scope={x['scope']}",
                f"--data={ws.file(item.id + '.data.json', x['data'])}",
                f"--source={x['ref']}", f"--source-revision={x['revision']}"]
    elif x["kind"] == "extract":
        args = ["compose", at, "--class=attributive", f"--content={x['content']}", f"--scope={x['scope']}",
                *[f"--depends={d}" for d in x["depends"]], f"--data={ws.file(item.id + '.data.json', x['data'])}",
                f"--source={x['source']}", f"--source-locator={x['locator']}", f"--source-note={x['note']}"]
    elif x["kind"] == "check":
        args = ["compose", at, "--class=formal", f"--content={x['content']}", f"--scope={x['scope']}",
                *[f"--depends={d}" for d in x["depends"]]]
    elif x["kind"] == "verdict":
        args = ["annotate", at, "--kind=verdict", f"--target={x['target']}", "--finding=affirm",
                "--aspect=provenance", f"--scope={x['scope']}"]
    else:
        args = ["annotate", at, "--kind=attests", f"--target={x['key']}", "--claim=is-automated",
                f"--about={x['key']}", "--scope=unconditional",
                "--method=The key is used only by review_tooling/sources to sign what a language model read from a paper."]
    return ws.run(*args).split()[0]


def gather(path: Path | None = None) -> Plan:
    """Gather the checked part of the registry by the rules above."""
    reg = sources.registry(path)
    gathered = load_gathered()
    root = Path(tempfile.mkdtemp(prefix="sources-"))
    try:
        ws = Workspace(pub_binary(), root)
        first = not gathered["items"]
        secrets = use_keys(ws, gathered, reg["created"])
        fetch(gathered, OBJECTS)
        ws.hold(OBJECTS)
        its = items(reg)
        p = plan(its, gathered)
        cids = known_cids(gathered)
        recorded = gathered["items"]
        made: list[tuple[str, str]] = []

        def make(name: str, *args: str) -> str:
            ws.become(gathered["keys"][name], secrets[name])
            cid = ws.run(*args).split()[0]
            ws.run("sign", f"--purpose={PURPOSE}", f"--target={cid}", f"--created={fg.now()}")
            made.append((name, cid))
            return cid

        for item in its:
            if item.id in p.keep:
                continue
            created = reg["created"] if item.id in p.compose and first else fg.now()
            name = signer(item)
            ws.become(gathered["keys"][name], secrets[name])
            cid = compose(ws, item, cids, created)
            ws.run("sign", f"--purpose={PURPOSE}", f"--target={cid}", f"--created={fg.now()}")
            if item.id in p.revise:
                old = recorded[item.id]["cid"]
                make(name, "relate", f"--created={created}", "--kind=supersedes", f"--from={cid}", f"--to={old}",
                     "--scope=unconditional")
            cids[item.id] = cid
            recorded[item.id] = {"cid": cid, "digest": item.digest(cids)}
        for item_id in p.retract:
            name = "extractor.claude" if item_id.startswith(("e.", "ch.")) else "reader.david"
            ws.become(gathered["keys"][name], secrets[name])
            for cid in fg.withdraw(ws, item_id, recorded[item_id]["cid"], fg.now(), NAME):
                ws.run("sign", f"--purpose={PURPOSE}", f"--target={cid}", f"--created={fg.now()}")
            recorded[item_id]["retracted"] = True
        added = fg.export(ws, OBJECTS)
        for name in added:
            gathered["objects"].setdefault(name, None)
        write(gathered)
        print(f"kept {len(p.keep)}, composed {len(p.compose)}, revised {len(p.revise)}, "
              f"retracted {len(p.retract)}; {len(added)} object(s) added to {OBJECTS}; "
              f"run `python -m review_tooling.sources upload` next")
        return p
    finally:
        shutil.rmtree(root, ignore_errors=True)


COMMENT = ("Written by `python -m review_tooling.sources gather` and `upload`; do not edit. The key each name in "
           "keys.json became; for each gathered item, the digest of its input and the object it became; "
           "each object's address on storage.sustenaut.eu (null until uploaded); and each open-licence "
           "PDF's address. `python -m review_tooling.sources check` compares it with sources.json.")


def write(gathered: dict) -> None:
    body = {k: gathered[k] for k in ("keys", "items", "objects", "files")}
    if gathered.get("index"):
        body["index"] = gathered["index"]
    GATHERED.write_text(json.dumps({"comment": COMMENT, **body}, indent=1, sort_keys=False) + "\n")
