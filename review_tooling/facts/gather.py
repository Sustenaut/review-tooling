"""Compose and sign the facts registry into corpus/objects/.

Run on the laptop that holds the signing key, with a `pub` built from
publet at the revision the Makefile pins (`make pub`):

    make gather                       # or: python -m tools.facts gather

The key's secret is read from $FACTS_KEY (default
~/.config/sustenaut/facts-signing.key) and never written into the
repository; corpus/key names the key object, which is public and kept in
corpus/objects/ with the rest. The first gather, with no key yet, makes one
and saves its secret there.

Every object is composed in a scratch workspace with the registry's
`created` time and the key as author, so an unchanged registry gives the
same identifiers each time. Each is then signed (purpose `authored`), and
corpus/objects/ is replaced by what the workspace holds, without its
policy. A singular's latitude and longitude become an H3 cell here, at the
registry's `h3_resolution`, with the h3 package: cells are computed, never
typed by hand.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from tools.facts import OBJECTS, REGISTRY, ROOT, registry

KEY_ID = ROOT / "corpus" / "key"
KEY_LABEL = "Sustenaut facts"
PURPOSE = "authored"


class GatherError(RuntimeError):
    pass


def pub_binary() -> str:
    built = ROOT / ".pub" / "bin" / "pub"
    return os.environ.get("PUB") or (str(built) if built.exists() else "pub")


def key_path() -> Path:
    return Path(os.environ.get("FACTS_KEY", Path.home() / ".config/sustenaut/facts-signing.key"))


class Workspace:
    def __init__(self, pub: str, root: Path, created: str):
        self.pub, self.root, self.created = pub, root, created

    def run(self, *args: str) -> str:
        out = subprocess.run([self.pub, *args], cwd=self.root, capture_output=True, text=True)
        if out.returncode != 0:
            raise GatherError(f"pub {' '.join(args[:2])}: {out.stderr.strip()}")
        return out.stdout

    def cid(self, *args: str) -> str:
        return self.run(*args, f"--created={self.created}").split()[0]

    def file(self, name: str, value) -> str:
        path = self.root / name
        path.write_text(json.dumps(value))
        return str(path)


def use_key(ws: Workspace) -> str:
    """Make the workspace author as the facts key, making the key if none exists."""
    ws.run("init")
    secret = key_path()
    if secret.exists() and KEY_ID.exists():
        key = KEY_ID.read_text().strip()
        (ws.root / ".publet" / "signing.key").write_text(secret.read_text())
        config = ws.root / ".publet" / "config"
        config.write_text("".join(f"author={key}\n" if line.startswith("author=") else line + "\n"
                                  for line in config.read_text().splitlines()))
        return key
    if secret.exists() != KEY_ID.exists():
        raise GatherError(f"{secret} and {KEY_ID} must both exist or neither: one is missing")
    out = ws.run("sign", "--generate-key", "--principal=organization", f"--label={KEY_LABEL}",
                 f"--created={ws.created}")
    key = next(line.split()[1] for line in out.splitlines() if line.startswith("key "))
    secret.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ws.root / ".publet" / "signing.key", secret)
    secret.chmod(0o600)
    KEY_ID.write_text(key + "\n")
    return key


def resolve(value, cids: dict[str, str]):
    """Replace the registry's local ids with CIDs wherever a CID belongs."""
    if isinstance(value, str):
        return cids.get(value, value)
    if isinstance(value, list):
        return [resolve(v, cids) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, cids) for k, v in value.items()}
    return value


def cell(location: dict | None, resolution: int) -> str | None:
    if not location:
        return None
    import h3

    return h3.latlng_to_cell(location["lat"], location["lng"], location.get("resolution", resolution))


def compose_all(ws: Workspace, reg: dict) -> dict[str, str]:
    cids: dict[str, str] = {}
    pdc = {d["id"]: d["defines"]["pdc"] for d in reg["definitions"] if d["defines"]["sort"] == "concept"}

    def define(item: dict, defines: dict) -> str:
        args = ["compose", "--class=definitional", f"--content={item['content']}", "--scope=unconditional",
                f"--defines={ws.file(item['id'] + '.json', resolve(defines, cids))}"]
        args += [f"--depends={cids[d]}" for d in item.get("depends", [])]
        return ws.cid(*args)

    for d in reg["definitions"]:
        cids[d["id"]] = define(d, d["defines"])
    for p in reg["procedures"]:
        cids[p["id"]] = ws.cid("compose", f"--class={p['class']}", f"--content={p['content']}",
                               "--scope=unconditional")
    for s in reg["singulars"]:
        defines = {"sort": "singular", "name": s["name"], "class": pdc[s["class"]]}
        if s.get("part_of"):
            defines["part_of"] = s["part_of"]
        h3_cell = cell(s.get("location"), reg["h3_resolution"])
        if h3_cell:
            defines["location"] = {"h3": h3_cell}
        cids[s["id"]] = define(s, defines)
    by_unit = {"m2": "k.area", "har": "k.area", "A": "k.current", "mm": "k.length", "m": "k.length",
               "[EUR]": "k.price"}
    for f in reg["facts"]:
        column = {"name": "value", "unit": f["unit"], "kind": cids[by_unit[f["unit"]]],
                  "concept": pdc[f["concept"]]}
        if f["unit"] == "[EUR]":
            column["unit_defs"] = {"[EUR]": cids["eur"]}
        value = f["value"]
        if f.get("measure"):
            column["measure"] = cids[f["measure"]]
            value = value if isinstance(value, dict) else {"value": value}
        data = {"columns": [{"name": "about"}, column], "rows": [[cids[f["about"]], value]]}
        args = ["compose", "--class=empirical", f"--content={f['content']}", "--scope=unconditional",
                f"--method={cids[f['method']]}", f"--depends={cids['ucum']}",
                f"--depends={cids[f['concept']]}", f"--data={ws.file(f['id'] + '.data.json', data)}"]
        args += [f"--statement={cids[s]}" for s in f.get("statements", [])]
        for source in f.get("sources", []):
            args += [f"--source={source['ref']}", f"--source-locator={source['locator']}"]
        cids[f["id"]] = ws.cid(*args)
        for n, note in enumerate(f.get("open", [])):
            cids[f"{f['id']}#open{n}"] = ws.cid(
                "annotate", "--kind=assessment", f"--target={cids[f['id']]}", "--verdict=undetermined",
                f"--grounds={note}", "--scope=unconditional")
    return cids


def export(ws: Workspace, key: str) -> int:
    ws.run("bundle", f"--out={ws.root / 'export'}")
    exported = ws.root / "export" / "pub" / "v1" / "object"
    policy = next(line.split("=", 1)[1] for line in (ws.root / ".publet" / "config").read_text().splitlines()
                  if line.startswith("policy="))
    OBJECTS.mkdir(parents=True, exist_ok=True)
    # The key object was written by the first gather; a workspace using the
    # saved key does not hold it, so it is kept rather than replaced.
    kept = key.replace(":", "_") + ".cbor"
    for old in OBJECTS.glob("*.cbor"):
        if old.name != kept:
            old.unlink()
    written = 0
    for path in sorted(exported.iterdir()):
        if path.name != policy:
            (OBJECTS / (path.name.replace(":", "_") + ".cbor")).write_bytes(path.read_bytes())
            written += 1
    return written


def gather(path: Path = REGISTRY) -> None:
    reg = registry(path)
    root = Path(tempfile.mkdtemp(prefix="facts-"))
    try:
        ws = Workspace(pub_binary(), root, reg["created"])
        key = use_key(ws)
        cids = compose_all(ws, reg)
        for cid in cids.values():
            ws.run("sign", f"--purpose={PURPOSE}", f"--target={cid}", f"--created={ws.created}")
        written = export(ws, key)
        print(f"{len(cids)} objects composed and signed by {key}; {written} written to "
              f"{OBJECTS.relative_to(ROOT)}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
