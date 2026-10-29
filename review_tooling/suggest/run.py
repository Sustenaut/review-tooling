"""One run of the suggestion job (see tools/suggest/__init__.py).

`--state` is a copy of what the review bucket holds under `suggestions/`,
`reviews/` and `runs/`; `--out` receives, in the bucket's layout, what the
run adds. The workflow (.github/workflows/suggest.yml) copies one from the
bucket and the other to it, never replacing anything there.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools import suggest as sg
from tools.facts.gather import now
from tools.suggest import draft


def write(out: Path, path: str, data: bytes) -> None:
    p = out / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def requests(state_dir: Path, out: Path, *, pdc: dict[str, str] | None = None) -> list[Path]:
    """Write each group's request body to `out/<kind>.json`, for a Claude Code session to
    answer in place of the API: it writes `<kind>.draft.json` beside each, in the request's
    output schema, and `run --drafts=DIR` signs those."""
    reg = sg.registry()
    snap = sg.snapshot(reg, sg.gathered())
    state = sg.State.read(state_dir)
    pdc = sg.pdc_classes() if pdc is None else pdc
    concepts = state.concepts() + [{"pdc": c, "name": n, "status": "registry"}
                                   for c, n in sg.registry_concepts(reg).items()]
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for kind, group in sorted(sg.groups(snap, state.covered()).items()):
        path = out / f"{kind}.json"
        path.write_text(json.dumps(draft.request(group, pdc, concepts), indent=1, ensure_ascii=False))
        written.append(path)
    return written


class Drafts:
    """Answers from files a Claude Code session wrote (see `requests`), shaped like the API's."""

    def __init__(self, root: Path, version: str):
        self.root, self.version = root, version
        self.messages = self

    def create(self, **body):
        import types

        for path in sorted(self.root.glob("*.json")):
            if not path.name.endswith(".draft.json") and json.loads(path.read_text()) == body:
                answer = path.with_name(path.stem + ".draft.json")
                if answer.exists():
                    text = answer.read_text()
                    json.loads(text)
                    return types.SimpleNamespace(
                        stop_reason="end_turn", model=self.version,
                        content=[types.SimpleNamespace(type="text", text=text)], usage=types.SimpleNamespace())
                break
        return types.SimpleNamespace(stop_reason="missing", stop_details=None)


def run(state_dir: Path, out: Path, *, pdc: dict[str, str] | None = None, client=None,
        signer=None, log=print) -> dict:
    """Suggest concepts for what is not yet suggested. Returns the run's record."""
    reg, gathered = sg.registry(), sg.gathered()
    snap = sg.snapshot(reg, gathered)
    inputs = sg.canonical_json({"pdc": sg.PDC_REV, "extracts": snap})
    inputs_cid = sg.cid(inputs)
    state = sg.State.read(state_dir)
    if sg.filename(inputs_cid) in state.runs:
        log(f"nothing to do: a run on these inputs ({inputs_cid}) is recorded")
        return {"inputs": inputs_cid, "skipped": True}

    pdc = sg.pdc_classes() if pdc is None else pdc
    pending = state.covered()
    groups = sg.groups(snap, pending)
    used = state.codes() | set(sg.registry_concepts(reg))
    concepts = state.concepts() + [{"pdc": c, "name": n, "status": "registry"}
                                   for c, n in sg.registry_concepts(reg).items()]
    log(f"{len(snap)} extracts, {len(pending)} already in a suggestion, {len(groups)} groups by kind")

    # `concepts` stays as it was at the start, so every request is what `requests` wrote.
    if signer is None:
        from tools.suggest.sign import Signer

        signer = Signer()
    created = now()
    record = {"inputs": inputs_cid, "created": created, "model": sg.MODEL, "suggestions": [],
              "problems": [], "usage": {}}
    write(out, f"blobs/{sg.filename(inputs_cid)}", inputs)
    try:
        for kind, group in sorted(groups.items()):
            body = draft.request(group, pdc, concepts)
            prompt = draft.prompt_blob(body)
            try:
                result, version, usage = draft.call(body, client)
            except sg.SuggestError as e:
                record["problems"].append(f"{kind}: {e}")
                log(f"{kind}: {e}")
                continue
            for k, v in usage.items():
                record["usage"][k] = record["usage"].get(k, 0) + v
            made, problems = sg.suggestions_for(group, result, pdc, used)
            record["problems"] += [f"{kind}: {p}" for p in problems]
            write(out, f"blobs/{sg.filename(sg.cid(prompt))}", prompt)
            engine = {"model": sg.MODEL, "version": version, "prompt": sg.cid(prompt), "inputs": inputs_cid,
                      "sampling": {"effort": sg.EFFORT}}
            for s in made:
                blob = sg.candidate_blob(s)
                s["candidate"]["blob"] = sg.cid(blob)
                write(out, f"blobs/{sg.filename(s['candidate']['blob'])}", blob)
                ident = signer.sign(created, s["target"], {"finding": s["finding"], "refs": s["refs"],
                                                           "engine": engine, "candidate": s["candidate"]["blob"]})
                suggestion = {"id": ident, "created": created, "finding": s["finding"], "title": s["title"],
                              "priority": s["priority"], "engine": engine, "target": s["target"],
                              "refs": s["refs"], "candidate": s["candidate"]}
                write(out, f"suggestions/{sg.filename(ident)}.json",
                      json.dumps(suggestion, indent=1, ensure_ascii=False).encode())
                record["suggestions"].append(ident)
            log(f"{kind}: {len(group)} extracts, {len(made)} suggestion(s)"
                + (f", {len(problems)} problem(s)" if problems else ""))
        for name, data in signer.export().items():
            write(out, f"objects/{sg.filename(name)}.cbor", data)
    finally:
        if hasattr(signer, "close"):
            signer.close()
    if not record["problems"]:
        write(out, f"runs/{sg.filename(inputs_cid)}.json", json.dumps(record, indent=1).encode())
    else:
        # A run with problems is not recorded, so the next run on these inputs tries the
        # failed groups again; what it did suggest is covered and not asked twice.
        for p in record["problems"]:
            log(f"problem: {p}")
    return record
