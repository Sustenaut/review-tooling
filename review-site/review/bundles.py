"""Names for CIDs, read from the pub bundles baked into the image (`pub bundle`), so a page
can say "electrical energy per tray" rather than a hash. A CID the bundles don't hold is
shown as itself, linked to pub.sustenaut.eu."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

PUB_SITE = os.environ.get("PUB_SITE", "https://pub.sustenaut.eu")


@lru_cache(maxsize=4096)
def label(cid: str) -> str | None:
    root = os.environ.get("REVIEW_BUNDLES")
    if not root:
        return None
    path = Path(root) / "o" / f"{cid}.json"
    if not path.is_file():
        return None
    summary = json.loads(path.read_text()).get("summary", {})
    return summary.get("label") or summary.get("name")


def href(cid: str) -> str:
    return f"{PUB_SITE}/o/{cid}/"
