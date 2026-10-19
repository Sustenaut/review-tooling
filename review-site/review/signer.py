"""Signing a reviewer's drafts.

`Signer` is the seam the shared sign-in will move through: today
`HeldKeySigner` signs with a key the service holds for each reviewer; with
the wallet login, a key service the person unlocks in their session takes
its place, and nothing that calls `sign` changes.

`HeldKeySigner` drives `pub` (the publet CLI), so objects are made exactly
as anywhere else: for each batch it builds a throwaway workspace around
the reviewer's key, composes and signs the drafts in it for the purpose
`authored`, and exports the canonical bytes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from review.auth import Reviewer
from review.drafts import Classify, Compose, Draft, Relate
from review.keys import Links, Secrets

PURPOSE = "authored"
KEY_LABEL = "Sustenaut review key, held by the review service"


@dataclass
class Signed:
    """A batch of signed objects: `made` maps each draft's ref (or a description) to its
    CID, and `objects` holds every new object's bytes by CID, signatures and the key
    object among them."""

    key: str
    made: list[tuple[str, str]]
    objects: dict[str, bytes]


class Signer(Protocol):
    def sign(self, reviewer: Reviewer, drafts: list[Draft], created: str) -> Signed: ...


class PubError(Exception):
    pass


class HeldKeySigner:
    def __init__(self, pub: str, links: Links, secrets: Secrets):
        self.pub, self.links, self.secrets = pub, links, secrets

    def _run(self, workspace: Path, *args: str) -> str:
        out = subprocess.run([self.pub, *args], cwd=workspace, capture_output=True, text=True)
        if out.returncode != 0:
            raise PubError(f"pub {args[0]}: {out.stderr.strip()}")
        return out.stdout.split()[0] if out.stdout.split() else ""

    def _workspace(self, reviewer: Reviewer, root: Path, created: str) -> tuple[str, bool]:
        """A workspace signing as the reviewer's key: (key CID, whether the key is new)."""
        self._run(root, "init")
        key = self.links.key_for(reviewer.account)
        seed = self.secrets.get(key) if key else None
        if key and seed:
            (root / ".publet" / "signing.key").write_text(seed + "\n")
            config = root / ".publet" / "config"
            config.write_text("".join(
                f"author={key}\n" if line.startswith("author=") else line + "\n"
                for line in config.read_text().splitlines()))
            return key, False
        out = subprocess.run([self.pub, "sign", "--generate-key", "--principal=human",
                              f"--label={KEY_LABEL}", f"--created={created}"],
                             cwd=root, capture_output=True, text=True)
        if out.returncode != 0:
            raise PubError(f"pub sign --generate-key: {out.stderr.strip()}")
        key = next(line.split()[1] for line in out.stdout.splitlines() if line.startswith("key "))
        self.secrets.put(key, (root / ".publet" / "signing.key").read_text().strip())
        self.links.link(reviewer.account, key)
        return key, True

    def sign(self, reviewer: Reviewer, drafts: list[Draft], created: str) -> Signed:
        root = Path(tempfile.mkdtemp(prefix="review-"))
        try:
            key, new = self._workspace(reviewer, root, created)
            at = f"--created={created}"
            refs: dict[str, str] = {}
            made: list[tuple[str, str]] = []
            for d in drafts:
                if isinstance(d, Compose):
                    args = ["compose", at, f"--class={d.cls}", f"--content={d.content}",
                            "--scope=unconditional", *[f"--depends={x}" for x in d.depends]]
                    if d.defines is not None:
                        path = root / f"{d.ref}.defines.json"
                        path.write_text(json.dumps(d.defines))
                        args.append(f"--defines={path}")
                    cid = refs[d.ref] = self._run(root, *args)
                    made.append((d.ref, cid))
                elif isinstance(d, Classify):
                    cid = self._run(root, "annotate", at, "--kind=classifies", f"--target={d.target}",
                                    f"--subject={refs[d.subject]}", "--scope=unconditional")
                    made.append((f"classifies {d.target} under {d.subject}", cid))
                elif isinstance(d, Relate):
                    cid = self._run(root, "relate", at, f"--kind={d.kind}", f"--from={refs.get(d.source, d.source)}",
                                    f"--to={d.target}", "--scope=unconditional")
                    made.append((f"{d.kind} {d.target}", cid))
            for _, cid in list(made):
                self._run(root, "sign", at, f"--purpose={PURPOSE}", f"--target={cid}")
            self._run(root, "bundle", f"--out={root / 'export'}")
            exported = root / "export" / "pub" / "v1" / "object"
            policy = next(line.split("=", 1)[1] for line in (root / ".publet" / "config").read_text().splitlines()
                          if line.startswith("policy="))
            objects = {p.name: p.read_bytes() for p in exported.iterdir()
                       if p.name != policy and (new or p.name != key)}
            return Signed(key, made, objects)
        finally:
            shutil.rmtree(root, ignore_errors=True)


def from_env(links: Links, secrets: Secrets) -> Signer:
    return HeldKeySigner(os.environ.get("PUB", "pub"), links, secrets)
