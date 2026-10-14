"""Reviewers' keys, held by the service until people hold their own.

Two things are kept apart so either can be deleted on its own:

- the **link** from an account (an email) to the CID of the key it signs
  with, in its own table (Firestore collection `review_key_links`). This is
  the only place a person's identity meets their key; deleting the link
  leaves the signed publets standing, attributed to a key no one can name;
- the key's **secret**, in a bucket only the service reads
  (`<project>-review-keys`), by key CID.

Publets never carry the email (decided 2026-10-12: personal data stays out
of publets). Locally and in tests a directory holds both.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class Links(Protocol):
    def key_for(self, email: str) -> str | None: ...
    def link(self, email: str, key: str) -> None: ...
    def forget(self, email: str) -> None: ...


class Secrets(Protocol):
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, seed_hex: str) -> None: ...


class LocalLinks:
    def __init__(self, path: Path):
        self.path = path

    def _all(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def key_for(self, email: str) -> str | None:
        return self._all().get(email, {}).get("key")

    def link(self, email: str, key: str) -> None:
        links = self._all()
        links[email] = {"key": key, "linked": datetime.now(timezone.utc).isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(links, indent=1))

    def forget(self, email: str) -> None:
        links = self._all()
        links.pop(email, None)
        self.path.write_text(json.dumps(links, indent=1))


class LocalSecrets:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / f"{key.replace(':', '_')}.key"

    def get(self, key: str) -> str | None:
        p = self._path(key)
        return p.read_text().strip() if p.exists() else None

    def put(self, key: str, seed_hex: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        p = self._path(key)
        p.write_text(seed_hex + "\n")
        p.chmod(0o600)


class FirestoreLinks:
    COLLECTION = "review_key_links"

    def __init__(self, project: str):
        from google.cloud import firestore

        self.collection = firestore.Client(project=project).collection(self.COLLECTION)

    def key_for(self, email: str) -> str | None:
        doc = self.collection.document(email).get()
        return doc.to_dict().get("key") if doc.exists else None

    def link(self, email: str, key: str) -> None:
        self.collection.document(email).set({"key": key, "linked": datetime.now(timezone.utc)})

    def forget(self, email: str) -> None:
        self.collection.document(email).delete()


class BucketSecrets:
    def __init__(self, bucket: str):
        from review.store import BucketStore

        self.store = BucketStore(bucket)

    def get(self, key: str) -> str | None:
        data = self.store.read(f"{key.replace(':', '_')}.key")
        return data.decode().strip() if data is not None else None

    def put(self, key: str, seed_hex: str) -> None:
        self.store.create(f"{key.replace(':', '_')}.key", seed_hex.encode(), "text/plain")


def from_env() -> tuple[Links, Secrets]:
    bucket = os.environ.get("REVIEW_KEYS_BUCKET")
    if bucket:
        return FirestoreLinks(os.environ.get("GOOGLE_CLOUD_PROJECT", "sustenaut")), BucketSecrets(bucket)
    root = Path(os.environ.get("REVIEW_DIR", "build/review-store")) / "_keys"
    return LocalLinks(root / "links.json"), LocalSecrets(root)
