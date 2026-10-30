"""Paper extracts: values read from papers, each checkable against its page.

`corpus/sources/sources.json` holds source records (one per paper), the
systems the papers studied, the scenarios they model, one extract per
printed value, and check claims for a paper's own inconsistencies, as
publet's spec Section 5.9.8 describes them. An extract is drafted by a
model (`read_by: extractor.claude`) and gathered only once a person has
checked it against its page (`checked`), which is what
`python -m review_tooling.sources review` records.

    python -m review_tooling.sources add DOI PDF    # a new source record from the PDF and Crossref
    python -m review_tooling.sources lint           # the registry's shape; quotes against ~/papers/ where held
    python -m review_tooling.sources review         # the local check page
    python -m review_tooling.sources gather         # compose and sign what was checked (gather.py)
    python -m review_tooling.sources check          # gathered.json agrees with the registry (CI)
    python -m review_tooling.sources upload         # PDFs and objects to storage.sustenaut.eu (upload.py)

`lint` and `check` need neither pub, a key, nor the PDFs, so CI runs them
in tests/test_sources.py. Where a PDF is in ~/papers/ (named by its
SHA-256), `lint` also looks for each quote on the page its locator names.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from review_tooling import facts

ROOT = facts.ROOT
DIR = ROOT / "corpus" / "sources"
REGISTRY = DIR / "sources.json"
INTAKE = DIR / "intake.json"
KEYS = DIR / "keys.json"
GATHERED = DIR / "gathered.json"

QUOTE_WORDS = 25
LOCATOR = re.compile(r"^p (\d+)(; [^;]+)*$")
READ = ("text", "table", "figure")
SOURCE_TYPES = ("primary", "secondary", "review", "dataset")
# Licences under which a copy may be put on storage.sustenaut.eu.
OPEN_LICENCES = ("CC BY", "CC BY 4.0", "CC BY 3.0", "CC BY-SA 4.0", "CC0 1.0")
STORAGE = "https://storage.sustenaut.eu/sources"


def token() -> str:
    """What the private `sources` tenant takes: $SOURCES_TOKEN; in GitHub Actions, the
    job's OpenID Connect token (permissions: id-token: write); else `gh auth token`,
    which storage accepts if it can push to Sustenaut/business-plan."""
    if os.environ.get("SOURCES_TOKEN"):
        return os.environ["SOURCES_TOKEN"]
    if os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"):
        request = urllib.request.Request(
            os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=storage.sustenaut.eu",
            headers={"Authorization": f"bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"})
        with urllib.request.urlopen(request, timeout=30) as r:  # noqa: S310 (GitHub's own address)
            return json.load(r)["value"]
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()


def papers_dir() -> Path:
    return Path(os.environ.get("SOURCES_PAPERS", Path.home() / "papers"))


def registry(path: Path | None = None) -> dict:
    return json.loads((REGISTRY if path is None else path).read_text())


def save(reg: dict, path: Path | None = None) -> None:
    (REGISTRY if path is None else path).write_text(json.dumps(reg, indent=1, ensure_ascii=False) + "\n")


def is_open(source: dict) -> bool:
    return source.get("licence") in OPEN_LICENCES


def file_url(source: dict) -> str | None:
    """Where an open-licence copy is served: storage names a file by its SHA-256."""
    if not is_open(source):
        return None
    return f"{STORAGE}/f/{source['file_sha256']}/{source['file_name']}"


def page(extract: dict) -> int:
    return int(LOCATOR.match(extract["locator"]).group(1))


def status(extract: dict) -> str:
    """The status the spec's Section 5.9.7 computes, from what the registry says."""
    checked = extract.get("checked") or {}
    if checked.get("finding") == "reject":
        return "rejected"
    if extract.get("cites") and not extract.get("derived_from"):
        return "lead"
    if checked.get("finding") != "affirm":
        return "to-verify"
    return "estimate"


def words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.replace("…", " ").strip()) if w]


def normal(text: str) -> str:
    """Text as compared: compatibility forms (² as 2), soft hyphens and
    line-break hyphens gone, only letters and digits, single-spaced."""
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.lower()).split())


@dataclass(frozen=True)
class Problem:
    id: str
    message: str

    def __str__(self) -> str:
        return f"{self.id}: {self.message}"


def lint(reg: dict, papers: Path | None = None) -> list[Problem]:
    """Problems with the registry's shape, and quotes not found on their page."""
    out: list[Problem] = []
    defs = {d["id"]: d for d in reg["definitions"]}
    sources = {s["id"]: s for s in reg["sources"]}
    systems = {s["id"]: s for s in reg["systems"]}
    scenarios = {s["id"] for s in reg["scenarios"]}
    extracts = {e["id"]: e for e in reg["extracts"]}

    def known(ref: str) -> bool:
        return ref in defs

    def definition(ref: str) -> dict:
        return defs[ref]

    seen: set[str] = set()
    for group in ("definitions", "sources", "systems", "scenarios", "extracts", "checks"):
        for item in reg[group]:
            if item["id"] in seen:
                out.append(Problem(item["id"], "used twice"))
            seen.add(item["id"])
    for d in reg["definitions"]:
        out += [Problem(d["id"], f"depends on unknown {r}") for r in d.get("depends", []) if not known(r)]
    for s in reg["sources"]:
        if s["source_type"] not in SOURCE_TYPES:
            out.append(Problem(s["id"], f"source type is one of {', '.join(SOURCE_TYPES)}"))
        if not re.fullmatch(r"[0-9a-f]{64}", s["file_sha256"]):
            out.append(Problem(s["id"], "file_sha256 is 64 lowercase hexadecimal digits"))
        if not s.get("doi") and not s.get("url"):
            out.append(Problem(s["id"], "needs a doi or a url"))
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.pdf", s["file_name"]):
            out.append(Problem(s["id"], "file_name is lowercase letters, digits, dots and dashes, ending .pdf"))
    for s in reg["systems"]:
        if not known(s["class"]) or definition(s["class"])["defines"]["sort"] != "concept":
            out.append(Problem(s["id"], f"class {s['class']} is not a concept here"))
        if s.get("part_of") and s["part_of"] not in systems:
            out.append(Problem(s["id"], f"part of unknown {s['part_of']}"))
    for e in reg["extracts"]:
        bad = lambda message: out.append(Problem(e["id"], message))  # noqa: E731
        source = sources.get(e["source"])
        if source is None:
            bad(f"unknown source {e['source']}")
            continue
        if e["about"] is not None and e["about"] not in systems:
            bad(f"about unknown system {e['about']}")
        if e.get("scenario") and e["scenario"] not in scenarios:
            bad(f"unknown scenario {e['scenario']}")
        if not known(e["kind"]) or definition(e["kind"])["defines"]["sort"] != "kind":
            bad(f"kind {e['kind']} is not a kind here")
        elif e["unit"] not in definition(e["kind"])["defines"].get("units", [e["unit"]]):
            bad(f"unit {e['unit']} is not one of {e['kind']}'s")
        if not known(e["measure"]) or definition(e["measure"])["defines"]["sort"] != "measure":
            bad(f"measure {e['measure']} is not a measure here")
        else:
            names = {p["name"] for p in definition(e["measure"])["defines"]["parameters"]}
            if set(e["value"]) != names:
                bad(f"value has {sorted(e['value'])}, its measure {sorted(names)}")
            if any(not re.fullmatch(r"-?\d+(\.\d+)?", v) for v in e["value"].values()):
                bad("values are decimal strings")
        m = LOCATOR.match(e["locator"])
        if not m:
            bad('locator is "p N; ..." with the page index first')
        elif not 1 <= int(m.group(1)) <= source["pages"]:
            bad(f"page {m.group(1)} is not in the file's {source['pages']} pages")
        if e["read"] not in READ:
            bad(f"read is one of {', '.join(READ)}")
        if (e["read"] == "figure") != (e["measure"] == "m.read_from_figure"):
            bad('a value read off a figure, and only such a value, uses the measure "read from figure"')
        # The value is in the quote as printed, or `printed` says how it appears there.
        shown = e.get("printed") or " | ".join(e["value"].values())
        if not all(normal(v) and f" {normal(v)} " in f" {normal(e['quote'])} " for v in shown.split(" | ")):
            bad("the value is not in the quote: set `printed` to how the quote writes it")
        n = len(words(e["quote"]))
        if not 0 < n <= QUOTE_WORDS:
            bad(f"quote is 1 to {QUOTE_WORDS} words, not {n}")
        if e.get("derived_from") and e["derived_from"] not in extracts:
            bad(f"derived from unknown {e['derived_from']}")
        checked = e.get("checked")
        if checked is not None and (checked.get("finding") not in ("affirm", "reject") or not checked.get("by")
                                    or not checked.get("at")):
            bad("checked is null or {by, at, finding: affirm | reject}")
    for c in reg["checks"]:
        out += [Problem(c["id"], f"depends on unknown extract {d}") for d in c["depends"] if d not in extracts]
    out += quotes(reg, papers)
    return out


def page_texts(pdf: Path, number: int) -> list[str]:
    """The page as pdftotext reads it, in reading order and in layout."""
    if not shutil.which("pdftotext"):
        return []
    return [subprocess.run(["pdftotext", *mode, "-f", str(number), "-l", str(number), str(pdf), "-"],
                           capture_output=True, text=True).stdout for mode in ([], ["-layout"])]


def quote_on_page(quote: str, texts: list[str]) -> bool:
    """Each part of the quote (parts split at …) is on the page, in order."""
    parts = [normal(p) for p in quote.split("…") if normal(p)]
    for text in texts:
        text, at = normal(text), 0
        for part in parts:
            at = text.find(part, at)
            if at < 0:
                break
            at += len(part)
        else:
            return True
    return False


def quotes(reg: dict, papers: Path | None = None) -> list[Problem]:
    """Quotes not found on their page, for the PDFs held in `papers`."""
    papers = papers_dir() if papers is None else papers
    sources = {s["id"]: s for s in reg["sources"]}
    out, cache = [], {}
    for e in reg["extracts"]:
        source = sources.get(e["source"])
        if source is None or not LOCATOR.match(e["locator"]):
            continue
        pdf = papers / f"{source['file_sha256']}.pdf"
        if not pdf.exists():
            continue
        key = (pdf, page(e))
        if key not in cache:
            cache[key] = page_texts(pdf, page(e))
        if cache[key] and not quote_on_page(e["quote"], cache[key]):
            out.append(Problem(e["id"], f"quote not found on page {page(e)} of {pdf.name}"))
    return out
