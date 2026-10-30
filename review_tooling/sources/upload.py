"""Put what gather made on storage.sustenaut.eu, kept, and record where.

    make sources-upload            # or: python -m tools.sources upload

Uploads, to the `sources` tenant (Sustenaut/storage), every object in
corpus/sources/gathered.json not uploaded yet, the PDF of every gathered
source record whose licence allows it (from ~/papers/<sha256>.pdf), and an
index.json naming each item's object and address. Storage keeps every
file of this tenant and names it by the SHA-256 of its bytes, so an
upload repeated is the same file. The token is sources.token().
"""

from __future__ import annotations

import json
import urllib.request
import uuid
from pathlib import Path

from tools import sources
from tools.sources import gather

ENDPOINT = f"{sources.STORAGE}/files"
HOST = sources.STORAGE.rsplit("/", 1)[0]


def post(name: str, data: bytes, label: str, auth: str) -> dict:
    """One multipart upload: the file, its name, a label, kept."""
    boundary = uuid.uuid4().hex
    parts = []
    for field, value in (("name", name), ("label", label), ("kept", "true")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Authorization": f"Bearer {auth}", "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "sustenaut-sources"})
    with urllib.request.urlopen(request, timeout=120) as r:  # noqa: S310 (our own storage)
        return json.load(r)


def upload(papers: Path | None = None) -> dict:
    papers = sources.papers_dir() if papers is None else papers
    reg = sources.registry()
    gathered = gather.load_gathered()
    auth = sources.token()
    for name, url in sorted(gathered["objects"].items()):
        if not url:
            answer = post(name, (gather.OBJECTS / name).read_bytes(), "publet object", auth)
            gathered["objects"][name] = HOST + answer["url"]
            gather.write(gathered)
    for s in reg["sources"]:
        if s["id"] in gathered["items"] and sources.is_open(s) and not gathered["files"].get(s["id"]):
            answer = post(s["file_name"], (papers / f"{s['file_sha256']}.pdf").read_bytes(), s["short"], auth)
            if answer["id"] != s["file_sha256"]:
                raise SystemExit(f"{s['id']}: storage named the PDF {answer['id']}, not {s['file_sha256']}")
            gathered["files"][s["id"]] = HOST + answer["url"]
            gather.write(gathered)
    index = {"items": {k: {"cid": v["cid"], "url": gathered["objects"].get(gather.filename(v["cid"]))}
                       for k, v in gathered["items"].items() if not v.get("retracted")},
             "keys": gathered["keys"], "files": gathered["files"]}
    answer = post("index.json", json.dumps(index, indent=1, sort_keys=True).encode(), "sources index", auth)
    gathered["index"] = HOST + answer["url"]
    gather.write(gathered)
    print(f"uploaded; index at {gathered['index']}")
    return gathered
