"""The check page: each draft extract beside its page of the PDF.

    make sources-review            # or: python -m review_tooling.sources review [--port=8765]

Serves http://127.0.0.1:8765/ from this machine only. It shows one
extract at a time -- the value, unit, what it is about, the locator and
the quote -- next to the PDF from ~/papers/ opened at the locator's page,
and writes the reader's finding into corpus/sources/sources.json:

    a   affirm: the value is printed there, as recorded
    e   edit the value, unit, locator or quote, then affirm
    r   reject: the value is not there, or should not be an extract
    u   undo the finding
    j/k next and previous

A finding is `checked: {by, at, finding}` on the extract. Nothing is
signed here: `make sources-gather` signs a provenance verdict for each
affirmed extract with the reader key.
"""

from __future__ import annotations

import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from review_tooling import sources

READER = "reader.david"
EDITABLE = ("value", "unit", "quantity", "locator", "quote", "printed")


def rows(reg: dict) -> list[dict]:
    sources_ = {s["id"]: s for s in reg["sources"]}
    systems = {s["id"]: s for s in reg["systems"]}
    scenarios = {s["id"]: s["content"] for s in reg["scenarios"]}
    out = []
    for e in reg["extracts"]:
        s = sources_[e["source"]]
        out.append({**e, "status": sources.status(e), "short": s["short"], "sha": s["file_sha256"],
                    "page": sources.page(e), "about_name": systems.get(e["about"], {}).get("content"),
                    "scenario_text": scenarios.get(e.get("scenario"))})
    return out


def apply(reg: dict, ident: str, finding: str | None, edits: dict | None = None, today: str | None = None) -> list[str]:
    """Record a finding (or clear it) on one extract; problems leave the registry unchanged."""
    extract = next(e for e in reg["extracts"] if e["id"] == ident)
    before = json.loads(json.dumps(extract))
    for key, value in (edits or {}).items():
        if key not in EDITABLE:
            return [f"{key} cannot be edited here"]
        if key == "value" and isinstance(value, str):
            value = {next(iter(extract["value"])): value}
        if key == "printed" and not value:
            extract.pop("printed", None)
            continue
        extract[key] = value
    if "unit" in (edits or {}):
        # A unit its kind does not list moves the extract to the one kind that does.
        kinds = [d["id"] for d in reg["definitions"] if d["defines"]["sort"] == "kind"
                 and extract["unit"] in d["defines"].get("units", [])]
        current = next(d for d in reg["definitions"] if d["id"] == extract["kind"])
        if extract["unit"] not in current["defines"].get("units", []) and len(kinds) == 1:
            extract["kind"] = kinds[0]
    extract["checked"] = None if finding is None else {
        "by": READER, "at": today or dt.date.today().isoformat(), "finding": finding}
    problems = [str(p) for p in sources.lint(reg) if p.id == ident]
    if problems:
        extract.clear()
        extract.update(before)
    return problems


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<title>Extract check</title><meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{--bg:#f4f6f8;--fg:#18212a;--muted:#5b6672;--line:#d5dce3;--card:#fff;--ok:#1f6f43;--bad:#9b2c2c;--lead:#8a5a00;--mark:#fbe98a}
@media (prefers-color-scheme:dark){:root{--bg:#11161c;--fg:#e3e8ee;--muted:#98a5b2;--line:#2c3743;--card:#18202a;--ok:#6fd39b;--bad:#f19a9a;--lead:#e7b86a;--mark:#5c5420}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;height:100vh;display:grid;grid-template-columns:minmax(340px,30%) 1fr}
aside{overflow:auto;padding:16px;border-right:1px solid var(--line);display:grid;gap:12px;align-content:start}
iframe{width:100%;height:100vh;border:0;background:#888}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:14px;display:grid;gap:8px}
.value{font:600 1.7rem ui-monospace,monospace}.value mark{background:var(--mark);color:inherit;padding:0 3px}
dl{display:grid;grid-template-columns:6.5em 1fr;gap:4px 10px;margin:0;font-size:.9rem}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
blockquote{margin:0;border-left:3px solid var(--mark);padding-left:10px;font-style:italic}
.pill{display:inline-block;font:600 .72rem ui-monospace,monospace;text-transform:uppercase;padding:1px 7px;border-radius:3px;border:1px solid currentColor}
.affirm,.estimate{color:var(--ok)}.reject,.rejected{color:var(--bad)}.lead{color:var(--lead)}.to-verify{color:var(--muted)}
button{font:inherit;padding:6px 12px;border-radius:4px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
button.primary{background:var(--ok);color:#fff;border-color:var(--ok)}
.bar{display:flex;gap:8px;flex-wrap:wrap}.meta{color:var(--muted);font-size:.85rem}
form{display:none;gap:6px}form.open{display:grid}input,textarea{font:inherit;padding:5px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg)}
#err{color:var(--bad)}ol{margin:0;padding-left:1.4em;font-size:.85rem;max-height:30vh;overflow:auto}li{cursor:pointer}li.cur{font-weight:700}
@media (max-width:800px){body{grid-template-columns:1fr;height:auto}iframe{height:70vh}}
</style></head><body>
<aside>
 <div class=meta id=progress></div>
 <div class=card id=card></div>
 <div class=bar>
  <button class=primary onclick="mark('affirm')" title="a">Affirm (a)</button>
  <button onclick="toggleEdit()" title="e">Edit (e)</button>
  <button onclick="mark('reject')" title="r">Reject (r)</button>
  <button onclick="mark(null)" title="u">Undo (u)</button>
  <button onclick="go(-1)" title="k">&larr; (k)</button><button onclick="go(1)" title="j">&rarr; (j)</button>
 </div>
 <form id=edit onsubmit="event.preventDefault();mark('affirm',edits())">
  <label>Value <input id=f-value></label><label>Unit <input id=f-unit></label>
  <label>Quantity <input id=f-quantity></label>
  <label>Locator <input id=f-locator></label><label>Quote <textarea id=f-quote rows=3></textarea></label>
  <label>Printed as (when the quote writes the value differently) <input id=f-printed></label>
  <button class=primary>Save and affirm</button>
 </form>
 <div id=err></div>
 <ol id=list></ol>
</aside>
<iframe id=pdf title="the source PDF at the extract's page"></iframe>
<script>
let rows=[],i=0;
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(keep){rows=await (await fetch('api/extracts')).json();if(!keep){const n=rows.findIndex(r=>!r.checked);i=n<0?0:n}show()}
function shown(v){return 'low' in v?v.low+' to '+v.high:('high' in v&&Object.keys(v).length==1)?'at most '+v.high:Object.values(v).join(' ± ')}
function show(){const r=rows[i];if(!r)return;const done=rows.filter(x=>x.checked).length;
 $('progress').textContent=`${i+1} of ${rows.length} · ${done} checked · ${r.short}`;
 const f=r.checked?r.checked.finding:'';
 $('card').innerHTML=`<div class=bar><span class="pill ${r.status}">${r.status}</span>${f?`<span class="pill ${f}">${f} · ${esc(r.checked.at)}</span>`:''}</div>
 <div class=meta>${esc(r.id)}</div><div class=value><mark>${esc(shown(r.value))}</mark> ${esc(r.unit)}</div>
 <dl><dt>Quantity</dt><dd>${esc(r.quantity)}</dd><dt>About</dt><dd>${esc(r.about_name||'(no system)')}</dd>
 <dt>Locator</dt><dd><code>${esc(r.locator)}</code> · read from ${esc(r.read)}</dd>${r.printed?`<dt>Printed</dt><dd>${esc(r.printed)}</dd>`:''}
 <dt>Scope</dt><dd>${esc(r.scope)}</dd>${r.scenario_text?`<dt>Scenario</dt><dd>${esc(r.scenario_text)}</dd>`:''}
 ${r.cites?`<dt>Cites</dt><dd>${esc(r.cites)}</dd>`:''}</dl><blockquote>${esc(r.quote)}</blockquote>`;
 $('pdf').src=`pdf/${r.sha}.pdf?x=${encodeURIComponent(r.id)}#page=${r.page}&view=FitH`;
 for(const k of ['unit','quantity','locator','quote','printed'])$('f-'+k).value=r[k]||'';$('f-value').value=Object.values(r.value).join(' ');
 $('list').innerHTML=rows.map((x,n)=>`<li class="${n==i?'cur':''}" onclick="i=${n};show()"><span class="${x.checked?x.checked.finding:x.status}">${x.checked?(x.checked.finding=='affirm'?'✓':'✗'):'·'}</span> ${esc(x.quantity)} — ${esc(shown(x.value))} ${esc(x.unit)}</li>`).join('');
 $('err').textContent=''}
function edits(){const r=rows[i],e={};for(const k of ['unit','quantity','locator','quote','printed']){const v=$('f-'+k).value.trim();if(v!==(r[k]||''))e[k]=v}
 const v=$('f-value').value.trim(),keys=Object.keys(r.value),parts=v.split(/\\s+/);
 if(v!==Object.values(r.value).join(' ')){if(parts.length!=keys.length){$('err').textContent='give '+keys.join(', ');throw 0}e.value=Object.fromEntries(keys.map((k,n)=>[k,parts[n]]))}return e}
function toggleEdit(){$('edit').classList.toggle('open')}
async function mark(finding,ed){const r=rows[i];const res=await fetch('api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:r.id,finding,edits:ed||{}})});
 const body=await res.json();if(!res.ok){$('err').textContent=body.problems.join('; ');return}$('edit').classList.remove('open');await load(true);if(finding)go(1)}
function go(d){i=Math.max(0,Math.min(rows.length-1,i+d));show()}
addEventListener('keydown',ev=>{if(ev.target.matches('input,textarea'))return;const k={a:()=>mark('affirm'),r:()=>mark('reject'),u:()=>mark(null),e:toggleEdit,j:()=>go(1),k:()=>go(-1)}[ev.key];if(k){ev.preventDefault();k()}});
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    papers: Path
    registry_path: Path | None = None

    def local(self, value: str | None, prefix: str = "") -> bool:
        """Whether a Host or Origin header names this page, not another site. Listening
        on 127.0.0.1 doesn't stop a page in the browser from sending requests here."""
        port = self.server.server_address[1]
        return value in (f"{prefix}127.0.0.1:{port}", f"{prefix}localhost:{port}")

    def send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self.local(self.headers.get("Host")):
            self.send(403, b"not this page's address", "text/plain")
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/extracts":
            self.send(200, json.dumps(rows(sources.registry(self.registry_path))).encode(), "application/json")
        elif path.startswith("/pdf/") and path.endswith(".pdf"):
            sha = path[5:-4]
            pdf = self.papers / f"{sha}.pdf"
            if len(sha) == 64 and all(c in "0123456789abcdef" for c in sha) and pdf.exists():
                self.send(200, pdf.read_bytes(), "application/pdf")
            else:
                self.send(404, b"no such PDF", "text/plain")
        else:
            self.send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        # A finding here is signed later with the reader's key, so only this page may send one.
        if (not self.local(self.headers.get("Host")) or not self.local(self.headers.get("Origin"), "http://")
                or self.headers.get("Content-Type") != "application/json"):
            self.send(403, b"only this page may record a finding", "text/plain")
            return
        if self.path != "/api/check":
            self.send(404, b"not found", "text/plain")
            return
        ask = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        reg = sources.registry(self.registry_path)
        if ask.get("finding") not in ("affirm", "reject", None) or not any(e["id"] == ask.get("id") for e in reg["extracts"]):
            self.send(400, json.dumps({"problems": ["unknown extract or finding"]}).encode(), "application/json")
            return
        problems = apply(reg, ask["id"], ask["finding"], ask.get("edits"))
        if problems:
            self.send(400, json.dumps({"problems": problems}).encode(), "application/json")
            return
        sources.save(reg, self.registry_path)
        self.send(200, b"{}", "application/json")

    def log_message(self, *_) -> None:
        pass


def serve(port: int = 8765, papers: Path | None = None) -> None:
    Handler.papers = sources.papers_dir() if papers is None else papers
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"check page at http://127.0.0.1:{port}/ (Ctrl-C to stop); findings go to {sources.REGISTRY}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
