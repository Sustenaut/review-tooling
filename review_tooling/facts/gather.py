"""Compose and sign the facts registry into corpus/objects/, keeping what is there.

Run on the laptop that holds the signing key:

    make gather                       # or: python -m tools.facts gather
    PUB=path/to/pub make gather       # use a pub already built; skips `make pub`

The key's secret is read from $FACTS_KEY (default
~/.config/sustenaut/facts-signing.key) and never written into the
repository; corpus/key names the key object, which is public and kept in
corpus/objects/ with the rest. The first gather, with no key yet, makes one
and saves its secret there.

Signed objects are permanent, and a later pub may write different bytes for
the same input, so a gather never re-mints what is already published. The
rules, each tested in tests/test_facts.py:

1. Every registry item has an input: what it would be composed from, with
   the local ids it names replaced by the identifiers they became. The
   input's digest, and the object it became, are recorded in
   corpus/gathered.json.
2. An item whose input is unchanged keeps its object, whatever pub runs:
   nothing is composed for it, so moving the pin changes nothing.
3. An item whose input changed is composed again and the new object
   supersedes the old (`supersedes`, authored by the same key), so the
   lineage holds and `pub get` reads its head. An item citing it changes
   input too, and is revised in turn.
4. An item gone from the registry, or a fact's open note removed, is
   withdrawn with `retracts`; nothing is deleted.
5. Objects are only ever added to corpus/objects/.

`python -m tools.facts check` tests the same rules without pub or the key,
which is how CI catches a registry edit nobody gathered, or an object
nobody's registry explains.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tools.facts import OBJECTS, REGISTRY, ROOT, registry

KEY_ID = ROOT / "corpus" / "key"
GATHERED = ROOT / "corpus" / "gathered.json"
KEY_LABEL = "Sustenaut facts"
PURPOSE = "authored"
UNIT_KIND = {"m2": "k.area", "har": "k.area", "A": "k.current", "mm": "k.length", "m": "k.length",
             "[EUR]": "k.price"}


class GatherError(RuntimeError):
    pass


# --- inputs and digests: pure, and the same in `check` -----------------------


def resolve(value, cids: dict[str, str]):
    """Replace the registry's local ids with CIDs wherever a CID belongs."""
    if isinstance(value, str):
        return cids.get(value, value)
    if isinstance(value, list):
        return [resolve(v, cids) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, cids) for k, v in value.items()}
    return value


@dataclass
class Item:
    """One thing gather composes: its id, what it is, and what it is made of."""

    id: str
    kind: str          # define | procedure | fact | open
    payload: dict
    refs: list[str] = field(default_factory=list)

    def input(self, cids: dict[str, str]) -> dict:
        return {"kind": self.kind, **resolve(self.payload, {r: cids[r] for r in self.refs if r in cids})}

    def digest(self, cids: dict[str, str]) -> str:
        text = json.dumps(self.input(cids), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()


def items(reg: dict) -> list[Item]:
    """Every item the registry gathers into, in an order that composes each
    after what it cites. A location stays latitude and longitude here, so
    the digest does not depend on the H3 library; compose makes the cell."""
    out: list[Item] = []
    pdc = {d["id"]: d["defines"]["pdc"] for d in reg["definitions"] if d["defines"]["sort"] == "concept"}
    ids = {d["id"] for d in reg["definitions"]}
    for d in reg["definitions"]:
        deps = d.get("depends", [])
        out.append(Item(d["id"], "define", {"content": d["content"], "defines": d["defines"], "depends": deps},
                        deps + [v for v in json.dumps(d["defines"]).split('"') if v in ids]))
    for p in reg["procedures"]:
        out.append(Item(p["id"], "procedure", {"class": p["class"], "content": p["content"]}))
    for s in reg["singulars"]:
        defines = {"sort": "singular", "name": s["name"], "class": pdc[s["class"]]}
        refs = list(s.get("depends", []))
        if s.get("part_of"):
            defines["part_of"] = s["part_of"]
            refs.append(s["part_of"])
        if s.get("location"):
            loc = s["location"]
            defines["location"] = {"lat": loc["lat"], "lng": loc["lng"],
                                   "resolution": loc.get("resolution", reg["h3_resolution"])}
        out.append(Item(s["id"], "define", {"content": s["content"], "defines": defines,
                                            "depends": s.get("depends", [])}, refs))
    for f in reg["facts"]:
        column = {"name": "value", "unit": f["unit"], "kind": UNIT_KIND[f["unit"]], "concept": pdc[f["concept"]]}
        refs = [UNIT_KIND[f["unit"]], f["about"], f["method"], "ucum", f["concept"], *f.get("statements", [])]
        if f["unit"] == "[EUR]":
            column["unit_defs"] = {"[EUR]": "eur"}
            refs.append("eur")
        value = f["value"]
        if f.get("measure"):
            column["measure"] = f["measure"]
            refs.append(f["measure"])
            value = value if isinstance(value, dict) else {"value": value}
        out.append(Item(f["id"], "fact", {
            "content": f["content"], "method": f["method"], "depends": ["ucum", f["concept"]],
            "statements": f.get("statements", []), "sources": f.get("sources", []),
            "data": {"columns": [{"name": "about"}, column], "rows": [[f["about"], value]]}}, refs))
        for n, note in enumerate(f.get("open", [])):
            out.append(Item(f"{f['id']}#open{n}", "open", {"target": f["id"], "note": note}, [f["id"]]))
    return out


def cid_of(data: bytes) -> str:
    digest = base64.b32encode(hashlib.sha256(data).digest()).decode().lower().rstrip("=")
    return f"pub:sha2-256:{digest}"


def filename(cid: str) -> str:
    return cid.replace(":", "_") + ".cbor"


def load_gathered(path: Path | None = None) -> dict:
    path = GATHERED if path is None else path
    if path.exists():
        return json.loads(path.read_text())
    return {"items": {}, "objects": []}


@dataclass
class Plan:
    keep: list[str]
    compose: list[str]       # new items
    revise: list[str]        # changed items: composed again, superseding the old
    retract: list[str]       # recorded items the registry no longer has


def plan(its: list[Item], gathered: dict) -> Plan:
    """What a gather would do, decided from inputs alone: no pub involved."""
    recorded = gathered["items"]
    cids = {k: v["cid"] for k, v in recorded.items()}
    keep, compose, revise = [], [], []
    for item in its:
        before = recorded.get(item.id)
        if before and before["digest"] == item.digest(cids):
            keep.append(item.id)
            continue
        (revise if before else compose).append(item.id)
        # What cites a changed item has a changed input too, once it is
        # composed: mark the id unknown so dependents compare unequal.
        cids[item.id] = f"pending:{item.id}"
    present = {i.id for i in its}
    retract = [k for k, v in recorded.items() if k not in present and not v.get("retracted")]
    return Plan(keep, compose, revise, retract)


def check(reg: dict | None = None, gathered: dict | None = None, objects: Path | None = None) -> list[str]:
    """Problems with corpus/: what a gather would change, and files that do
    not belong. Empty means the registry, the record and the objects agree."""
    reg = registry() if reg is None else reg
    gathered = load_gathered() if gathered is None else gathered
    objects = OBJECTS if objects is None else objects
    problems = []
    p = plan(items(reg), gathered)
    problems += [f"{i}: in the registry but never gathered" for i in p.compose]
    problems += [f"{i}: changed in the registry since it was gathered" for i in p.revise]
    problems += [f"{i}: gathered but gone from the registry, and not retracted" for i in p.retract]
    files = {path.name for path in objects.glob("*.cbor")}
    listed = set(gathered["objects"])
    problems += [f"{name}: in corpus/objects but not recorded by any gather" for name in sorted(files - listed)]
    problems += [f"{name}: recorded but missing from corpus/objects" for name in sorted(listed - files)]
    for name in sorted(files & listed):
        if filename(cid_of((objects / name).read_bytes())) != name:
            problems.append(f"{name}: its bytes do not hash to its name")
    for i, entry in gathered["items"].items():
        if filename(entry["cid"]) not in listed:
            problems.append(f"{i}: its object {entry['cid']} is not recorded")
    return problems


# --- composing: needs pub, and the key to sign -------------------------------


def pub_binary() -> str:
    built = ROOT / ".pub" / "bin" / "pub"
    return os.environ.get("PUB") or (str(built) if built.exists() else "pub")


def store_binary(pub: str) -> str:
    beside = Path(pub).parent / "pub-store"
    return str(beside) if beside.exists() else "pub-store"


def key_path() -> Path:
    return Path(os.environ.get("FACTS_KEY", Path.home() / ".config/sustenaut/facts-signing.key"))


def now() -> str:
    return os.environ.get("FACTS_NOW") or dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Workspace:
    def __init__(self, pub: str, root: Path):
        self.pub, self.root = pub, root

    def run(self, *args: str) -> str:
        out = subprocess.run([self.pub, *args], cwd=self.root, capture_output=True, text=True)
        if out.returncode != 0:
            raise GatherError(f"pub {' '.join(args[:2])}: {out.stderr.strip()}")
        return out.stdout

    def file(self, name: str, value) -> str:
        path = self.root / name.replace("#", "_")
        path.write_text(json.dumps(value))
        return str(path)

    def hold(self, objects: Path) -> None:
        """Put every committed object in the workspace's store, so what is
        composed is checked against them and can supersede them."""
        store = store_binary(self.pub)
        for path in sorted(objects.glob("*.cbor")):
            out = subprocess.run([store, "--store=.publet/objects.redb", "add"], cwd=self.root,
                                 input=path.read_bytes(), capture_output=True)
            if out.returncode != 0:
                raise GatherError(f"pub-store add {path.name}: {out.stderr.decode().strip()}")


def use_key(ws: Workspace, created: str, *, sign: bool = True) -> str:
    """Make the workspace author as the facts key, making the key if none exists."""
    ws.run("init")
    secret = key_path()
    if KEY_ID.exists() and (secret.exists() or not sign):
        key = KEY_ID.read_text().strip()
        if sign:
            (ws.root / ".publet" / "signing.key").write_text(secret.read_text())
        config = ws.root / ".publet" / "config"
        config.write_text("".join(f"author={key}\n" if line.startswith("author=") else line + "\n"
                                  for line in config.read_text().splitlines()))
        return key
    if KEY_ID.exists() or secret.exists():
        raise GatherError(f"{secret} and {KEY_ID} must both exist or neither: one is missing")
    out = ws.run("sign", "--generate-key", "--principal=organization", f"--label={KEY_LABEL}",
                 f"--created={created}")
    key = next(line.split()[1] for line in out.splitlines() if line.startswith("key "))
    secret.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ws.root / ".publet" / "signing.key", secret)
    secret.chmod(0o600)
    KEY_ID.write_text(key + "\n")
    return key


def compose(ws: Workspace, item: Item, cids: dict[str, str], created: str) -> str:
    x = item.input(cids)
    at = f"--created={created}"
    location = x.get("defines", {}).get("location")
    if location and "lat" in location:
        import h3

        x["defines"]["location"] = {"h3": h3.latlng_to_cell(location["lat"], location["lng"],
                                                            location["resolution"])}
    if x["kind"] == "define":
        args = ["compose", at, "--class=definitional", f"--content={x['content']}", "--scope=unconditional",
                f"--defines={ws.file(item.id + '.json', x['defines'])}", *[f"--depends={d}" for d in x["depends"]]]
    elif x["kind"] == "procedure":
        args = ["compose", at, f"--class={x['class']}", f"--content={x['content']}", "--scope=unconditional"]
    elif x["kind"] == "fact":
        args = ["compose", at, "--class=empirical", f"--content={x['content']}", "--scope=unconditional",
                f"--method={x['method']}", *[f"--depends={d}" for d in x["depends"]],
                f"--data={ws.file(item.id + '.data.json', x['data'])}",
                *[f"--statement={s}" for s in x["statements"]]]
        for source in x["sources"]:
            args += [f"--source={source['ref']}", f"--source-locator={source['locator']}"]
    else:
        args = ["annotate", at, "--kind=assessment", f"--target={x['target']}", "--verdict=undetermined",
                f"--grounds={x['note']}", "--scope=unconditional"]
    return ws.run(*args).split()[0]


def withdraw(ws: Workspace, item_id: str, cid: str, created: str) -> list[str]:
    """Retract an object: a short claim saying why, and `retracts` from it."""
    why = ws.run("compose", f"--created={created}", "--class=normative", "--scope=unconditional",
                 f"--content=The facts registry no longer states {item_id}.").split()[0]
    rel = ws.run("relate", f"--created={created}", "--kind=retracts", f"--from={why}", f"--to={cid}",
                 "--scope=unconditional").split()[0]
    return [why, rel]


def gather(path: Path = REGISTRY, *, sign: bool = True) -> Plan:
    """Gather the registry into corpus/objects/ by the rules above.

    With sign=False nothing is signed or written: the plan is returned and,
    when nothing has been recorded yet, the record is adopted from the
    objects already committed (each item composed keylessly must match one).
    """
    reg = registry(path)
    gathered = load_gathered()
    root = Path(tempfile.mkdtemp(prefix="facts-"))
    try:
        its = items(reg)
        p = plan(its, gathered)
        ws = Workspace(pub_binary(), root)
        key = use_key(ws, reg["created"], sign=sign)
        ws.hold(OBJECTS)
        recorded = gathered["items"]
        cids = {k: v["cid"] for k, v in recorded.items()}
        first = not recorded
        made: list[str] = []
        for item in its:
            if item.id in p.keep:
                continue
            created = reg["created"] if item.id in p.compose and first else now()
            cid = compose(ws, item, cids, created)
            made.append(cid)
            if item.id in p.revise:
                old = recorded[item.id]["cid"]
                if item.kind == "open":
                    made += withdraw(ws, item.id, old, created)
                else:
                    made.append(ws.run("relate", f"--created={created}", "--kind=supersedes", f"--from={cid}",
                                       f"--to={old}", "--scope=unconditional").split()[0])
            cids[item.id] = cid
            recorded[item.id] = {"cid": cid, "digest": item.digest(cids)}
        for item_id in p.retract:
            made += withdraw(ws, item_id, recorded[item_id]["cid"], now())
            recorded[item_id]["retracted"] = True
        if not sign:
            if first:
                missing = [c for c in made if not (OBJECTS / filename(c)).exists()]
                if missing:
                    raise GatherError(f"cannot adopt: {len(missing)} item(s) do not match a committed object")
                gathered["objects"] = sorted(p.name for p in OBJECTS.glob("*.cbor"))
                GATHERED.write_text(json.dumps({"comment": COMMENT, "key": key, **gathered}, indent=1) + "\n")
            return p
        for cid in made:
            ws.run("sign", f"--purpose={PURPOSE}", f"--target={cid}", f"--created={now()}")
        added = export(ws)
        gathered["objects"] = sorted(set(gathered["objects"]) | added
                                     | {p.name for p in OBJECTS.glob("*.cbor")})
        GATHERED.write_text(json.dumps({"comment": COMMENT, "key": key, **gathered}, indent=1) + "\n")
        print(f"kept {len(p.keep)}, composed {len(p.compose)}, revised {len(p.revise)}, "
              f"retracted {len(p.retract)}; {len(added)} object(s) added to {OBJECTS}")
        return p
    finally:
        shutil.rmtree(root, ignore_errors=True)


COMMENT = ("Written by `make gather`; do not edit. For each registry item, the digest of the input it was "
           "composed from and the object it became; and every file in corpus/objects/. "
           "`python -m tools.facts check` compares it with the registry and the files.")


def export(ws: Workspace) -> set[str]:
    """Add the workspace's new objects to corpus/objects/, never removing one."""
    ws.run("bundle", f"--out={ws.root / 'export'}")
    exported = ws.root / "export" / "pub" / "v1" / "object"
    policy = next(line.split("=", 1)[1] for line in (ws.root / ".publet" / "config").read_text().splitlines()
                  if line.startswith("policy="))
    OBJECTS.mkdir(parents=True, exist_ok=True)
    added = set()
    for path in sorted(exported.iterdir()):
        target = OBJECTS / filename(path.name)
        if path.name != policy and not target.exists():
            target.write_bytes(path.read_bytes())
            added.add(target.name)
    return added
