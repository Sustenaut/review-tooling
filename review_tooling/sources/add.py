"""A new source record from a PDF and its Crossref record.

    python -m review_tooling.sources add 10.1000/example ~/Downloads/example.pdf

The PDF is copied to ~/papers/<sha256>.pdf (the original stays where it
was) and a source record is added to corpus/sources/sources.json with the
file's SHA-256 and page count and Crossref's citation and licence. What
Crossref cannot say is left for a person to fill in before the record is
gathered: the version read, the source type, and the licence the copy
itself prints, which is what decides whether it is uploaded.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from review_tooling import sources

CROSSREF = "https://api.crossref.org/works/"
LICENCES = {"by/4.0": "CC BY 4.0", "by/3.0": "CC BY 3.0", "by-sa/4.0": "CC BY-SA 4.0", "zero/1.0": "CC0 1.0"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pages(path: Path) -> int:
    out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"^Pages:\s+(\d+)", out, re.M).group(1))


def crossref(doi: str) -> dict:
    url = CROSSREF + urllib.parse.quote(doi)
    request = urllib.request.Request(url, headers={"User-Agent": "sustenaut-sources (https://sustenaut.eu)"})
    with urllib.request.urlopen(request, timeout=30) as r:  # noqa: S310 (a fixed https host)
        return json.load(r)["message"]


def citation(work: dict) -> tuple[str, str]:
    """An APA-style reference, and the short form used in an extract's content."""
    authors = work.get("author", [])
    names = [f"{a.get('family', '')}, {' '.join(p[0] + '.' for p in a.get('given', '').replace('-', ' ').split())}"
             for a in authors]
    year = (work.get("published", {}).get("date-parts") or [[None]])[0][0]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + ", & " + names[-1] if names else ""
    journal = (work.get("container-title") or [""])[0]
    volume, issue, article = work.get("volume"), work.get("issue"), work.get("article-number") or work.get("page")
    where = journal + (f", {volume}" if volume else "") + (f"({issue})" if issue else "") + (f", {article}" if article else "")
    title = (work.get("title") or [""])[0]
    family = [a.get("family", "") for a in authors]
    short = (family[0] if len(family) == 1 else f"{family[0]} and {family[1]}" if len(family) == 2
             else f"{family[0]} et al.") + f" ({year})"
    return f"{joined} ({year}). {title}. {where}.", short


def add(doi: str, pdf: str, reg_path: Path | None = None, papers: Path | None = None, work: dict | None = None) -> str:
    path = Path(pdf).expanduser()
    if not path.read_bytes().startswith(b"%PDF-"):
        raise SystemExit(f"{path} does not start with %PDF-")
    digest = sha256(path)
    papers = sources.papers_dir() if papers is None else papers
    papers.mkdir(parents=True, exist_ok=True)
    held = papers / f"{digest}.pdf"
    if not held.exists():
        shutil.copyfile(path, held)
    work = crossref(doi) if work is None else work
    full, short = citation(work)
    licence = next((name for url in (x.get("URL", "") for x in work.get("license", []))
                    for key, name in LICENCES.items() if key in url), None)
    family = re.sub(r"[^a-z]+", "-", (work.get("author") or [{}])[0].get("family", "source").lower()).strip("-")
    year = short.rsplit("(", 1)[1].rstrip(")")
    reg = sources.registry(reg_path)
    ident = f"src.{family.replace('-', '_')}_{year}"
    if any(s["id"] == ident or s["file_sha256"] == digest for s in reg["sources"]):
        raise SystemExit(f"{ident} or a source with this file is already in the registry")
    reg["sources"].append({
        "id": ident, "short": short, "citation": full, "doi": doi, "url": None,
        "version": "TO FILL: the version read, such as version of record", "source_type": "primary",
        "licence": licence or "TO FILL: the licence the copy prints",
        "licence_line": "TO FILL: the licence line as the copy prints it",
        "file_sha256": digest, "file_name": f"{family}-{year}.pdf", "pages": pages(held)})
    sources.save(reg, reg_path)
    return f"{ident}: {held} ({digest}), {pages(held)} pages; fill in version, source type and licence"
