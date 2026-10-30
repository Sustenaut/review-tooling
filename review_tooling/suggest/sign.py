"""Step 4: sign each suggestion as a `triage` annotation with the suggester key.

`pub annotate` does not write triage annotations (publet-cli v0.2.2), so
the annotation is encoded here, in the shape `pub annotate` gives every
annotation, and handed to pub: `pub-store add` refuses bytes that are not
canonical (spec Section 4.1), and `pub sign` signs it as any other object.
publet's own reader (`Triage::from_object`) reads it back.

The suggester key is one per model (corpus/suggest/keys.json). Its secret
is never in the repository: the job reads it from $SUGGESTER_KEY, or from
the file keys.json names. The key object is committed beside keys.json so
each run can put it in its workspace and upload it with the suggestions.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from review_tooling.facts.gather import pub_binary, require_pub, store_binary
from review_tooling.suggest import DIR, KEYS, MODEL, SuggestError, cid, filename, keys

PURPOSE = "authored"


# --- canonical CBOR (spec Section 4.1) ---------------------------------------


def _head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([major << 5 | n])
    for size, extra in ((0xFF, 24), (0xFFFF, 25), (0xFFFFFFFF, 26), (0xFFFFFFFFFFFFFFFF, 27)):
        if n <= size:
            return bytes([major << 5 | extra]) + n.to_bytes(1 << (extra - 24), "big")
    raise ValueError(n)


def cbor(value) -> bytes:
    """Canonical CBOR for what an object holds: text, unsigned integers, arrays, and
    maps with text keys sorted by their UTF-8 bytes. No floats, as the spec requires."""
    if isinstance(value, bool) or value is None:
        return bytes([0xF5 if value is True else 0xF4 if value is False else 0xF6])
    if isinstance(value, int) and value >= 0:
        return _head(0, value)
    if isinstance(value, str):
        data = value.encode()
        return _head(3, len(data)) + data
    if isinstance(value, list):
        return _head(4, len(value)) + b"".join(cbor(v) for v in value)
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0].encode())
        return _head(5, len(items)) + b"".join(cbor(k) + cbor(v) for k, v in items)
    raise TypeError(f"no canonical CBOR for {type(value).__name__}")


def triage(author: str, created: str, target: str, value: dict) -> bytes:
    return cbor({
        "pub": "1",
        "type": "claim.annotation",
        "author": author,
        "created": created,
        "body": {"kind": "triage", "target": target, "value": value,
                 "scope": {"domain": "unconditional", "conditions": []}},
    })


# --- the key -----------------------------------------------------------------


def secret_path(model: str = MODEL) -> Path:
    return Path.home() / ".config" / "sustenaut" / f"suggester-{model}.key"


def key_object_path(key: str) -> Path:
    return DIR / "keys" / (filename(key) + ".cbor")


def make_key(created: str, model: str = MODEL) -> str:
    """Make the suggester key for `model`: the secret to ~/.config/sustenaut/, the key
    object and its identifier to corpus/suggest/. Run once per model, on the laptop."""
    known = keys()
    if model in known:
        raise SuggestError(f"{model} already has a suggester key: {known[model]['key']}")
    secret = secret_path(model)
    if secret.exists():
        raise SuggestError(f"{secret} exists but keys.json has no key for {model}")
    pub = checked_pub()
    root = Path(tempfile.mkdtemp(prefix="suggest-key-"))
    try:
        run(pub, root, "init")
        out = run(pub, root, "sign", "--generate-key", "--principal=automated",
                  f"--label=Sustenaut suggester ({model})", f"--created={created}")
        key = next(line.split()[1] for line in out.splitlines() if line.startswith("key "))
        run(pub, root, "bundle", f"--out={root / 'export'}")
        obj = root / "export" / "pub" / "v1" / "object" / key
        key_object_path(key).parent.mkdir(parents=True, exist_ok=True)
        key_object_path(key).write_bytes(obj.read_bytes())
        secret.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / ".publet" / "signing.key", secret)
        secret.chmod(0o600)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    known[model] = {"key": key, "principal": "automated", "secret": f"~/.config/sustenaut/suggester-{model}.key",
                    "signs": f"triage annotations drafted by {model}"}
    KEYS.write_text(json.dumps(known, indent=1, ensure_ascii=False) + "\n")
    return key


def checked_pub() -> str:
    """The pub to use, once it meets pub-version.txt."""
    return require_pub(pub_binary())


def run(pub: str, root: Path, *args: str) -> str:
    out = subprocess.run([pub, *args], cwd=root, capture_output=True, text=True)
    if out.returncode != 0:
        raise SuggestError(f"pub {' '.join(args[:2])}: {out.stderr.strip()}")
    return out.stdout


class Signer:
    """A throwaway pub workspace authoring as the suggester key."""

    def __init__(self, model: str = MODEL, secret: str | None = None):
        known = keys().get(model)
        if known is None:
            raise SuggestError(f"no suggester key for {model}: run `python -m review_tooling.suggest key` on the laptop")
        self.key = known["key"]
        if secret is None:
            secret = os.environ.get("SUGGESTER_KEY")
        if not secret:
            path = Path(os.path.expanduser(known["secret"]))
            if not path.exists():
                raise SuggestError(f"the suggester key's secret is in neither $SUGGESTER_KEY nor {path}")
            secret = path.read_text()
        self.pub = checked_pub()
        self.store = store_binary(self.pub)
        self.root = Path(tempfile.mkdtemp(prefix="suggest-"))
        run(self.pub, self.root, "init")
        (self.root / ".publet" / "signing.key").write_text(secret.strip() + "\n")
        config = self.root / ".publet" / "config"
        config.write_text("".join(f"author={self.key}\n" if line.startswith("author=") else line + "\n"
                                  for line in config.read_text().splitlines()))
        self.add(key_object_path(self.key).read_bytes())
        self.made: list[str] = []

    def add(self, data: bytes) -> None:
        out = subprocess.run([self.store, "--store=.publet/objects.redb", "add"], cwd=self.root,
                             input=data, capture_output=True)
        if out.returncode != 0:
            raise SuggestError(f"pub-store add: {out.stderr.decode().strip()}")

    def sign(self, created: str, target: str, value: dict) -> str:
        """Sign one triage; its identifier."""
        data = triage(self.key, created, target, value)
        ident = cid(data)
        self.add(data)
        run(self.pub, self.root, "sign", f"--created={created}", f"--purpose={PURPOSE}", f"--target={ident}")
        self.made.append(ident)
        return ident

    def export(self) -> dict[str, bytes]:
        """Every object made, their signatures, and the key object, by identifier."""
        run(self.pub, self.root, "bundle", f"--out={self.root / 'export'}")
        policy = next(line.split("=", 1)[1] for line in (self.root / ".publet" / "config").read_text().splitlines()
                      if line.startswith("policy="))
        exported = self.root / "export" / "pub" / "v1" / "object"
        return {p.name: p.read_bytes() for p in exported.iterdir() if p.name != policy}

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
