"""Where suggestions and reviews live.

A bucket only ever added to: the service may create objects and read them,
never replace or delete one (infra/review-site.tf), so a signed review
stays as it was signed. Paths:

    suggestions/<id>.json        a suggestion, written by the suggestion job
    objects/<cid>.cbor           a signed object: concept, membership, relation, sig, key
    reviews/<when>-<id>.json     what a reviewer did with a suggestion, and the objects it made

Locally and in tests a directory stands in for the bucket.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol
from urllib.parse import quote


class Exists(Exception):
    """The path is taken: nothing in the store is ever replaced."""


class Store(Protocol):
    def list(self, prefix: str) -> list[str]: ...
    def read(self, path: str) -> bytes | None: ...
    def create(self, path: str, data: bytes, content_type: str) -> None: ...


def object_path(cid: str) -> str:
    return f"objects/{cid.replace(':', '_')}.cbor"


class LocalStore:
    def __init__(self, root: Path):
        self.root = root

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.is_dir():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

    def read(self, path: str) -> bytes | None:
        p = self.root / path
        return p.read_bytes() if p.is_file() else None

    def create(self, path: str, data: bytes, content_type: str) -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with p.open("xb") as f:
                f.write(data)
        except FileExistsError:
            raise Exists(path) from None


class BucketStore:
    """A Cloud Storage bucket through its JSON API, with the service's own credentials."""

    API = "https://storage.googleapis.com/storage/v1/b"
    UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b"

    def __init__(self, bucket: str):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
        self.session = AuthorizedSession(credentials)
        self.bucket = bucket

    def list(self, prefix: str) -> list[str]:
        names, token = [], None
        while True:
            params = {"prefix": prefix, "fields": "items(name),nextPageToken"}
            if token:
                params["pageToken"] = token
            r = self.session.get(f"{self.API}/{self.bucket}/o", params=params)
            r.raise_for_status()
            body = r.json()
            names += [i["name"] for i in body.get("items", [])]
            token = body.get("nextPageToken")
            if not token:
                return sorted(names)

    def read(self, path: str) -> bytes | None:
        r = self.session.get(f"{self.API}/{self.bucket}/o/{quote(path, safe='')}", params={"alt": "media"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def create(self, path: str, data: bytes, content_type: str) -> None:
        # ifGenerationMatch=0: only if nothing is there yet.
        r = self.session.post(f"{self.UPLOAD}/{self.bucket}/o",
                              params={"uploadType": "media", "name": path, "ifGenerationMatch": "0"},
                              data=data, headers={"Content-Type": content_type})
        if r.status_code == 412:
            raise Exists(path)
        r.raise_for_status()


def from_env() -> Store:
    bucket = os.environ.get("REVIEW_BUCKET")
    if bucket:
        return BucketStore(bucket)
    return LocalStore(Path(os.environ.get("REVIEW_DIR", "build/review-store")))


def read_json(store: Store, path: str):
    data = store.read(path)
    return json.loads(data) if data is not None else None
