"""The review service: one suggestion per page, editable in place, signed by a person.

    make review            # run it locally on http://localhost:8001 with the fake suggestions
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from review import auth, bundles, keys, reviews, signer, store

HERE = Path(__file__).resolve().parent
templates = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape())
templates.globals.update(label=bundles.label, href=bundles.href, actions=reviews.ACTIONS,
                         wallet_offered=auth.wallet_offered, quote=quote)

app = FastAPI(title="Sustenaut reviews", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.include_router(auth.router)  # /login, /auth/callback, /logout: the wallet sign-in


class Services:
    """The store and the signer, made once; tests replace them."""

    store: store.Store | None = None
    signer: signer.Signer | None = None

    @classmethod
    def get(cls) -> tuple[store.Store, signer.Signer]:
        if cls.store is None:
            cls.store = store.from_env()
        if cls.signer is None:
            cls.signer = signer.from_env(*keys.from_env())
        return cls.store, cls.signer


def page(name: str, status_code: int = 200, **values) -> HTMLResponse:
    return HTMLResponse(templates.get_template(name).render(**values), status_code=status_code)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def queue(who: auth.Reviewer = Depends(auth.reviewer)):
    s, _ = Services.get()
    return page("queue.html", who=who, rows=reviews.queue(s), rates=reviews.overturned(s))


def _suggestion(sid: str) -> dict:
    s, _ = Services.get()
    found = reviews.suggestion(s, sid)
    if not found:
        raise HTTPException(404, "No such suggestion")
    return found


@app.get("/s/{sid}", response_class=HTMLResponse)
def show(sid: str, who: auth.Reviewer = Depends(auth.reviewer)):
    s, _ = Services.get()
    found = _suggestion(sid)
    history = [r for r in reviews.reviews(s) if r["suggestion"] == sid]
    return page("suggestion.html", who=who, s=found, history=history, state=reviews.status(history))


@app.post("/s/{sid}", dependencies=[Depends(auth.same_origin)])
async def act(sid: str, request: Request, who: auth.Reviewer = Depends(auth.reviewer)):
    s, sign = Services.get()
    found = _suggestion(sid)
    form = parse_qs((await request.body()).decode(), keep_blank_values=True)
    action = (form.get("action") or [""])[0]
    try:
        reviews.record(s, sign, who, found, action, form)
    except reviews.Invalid as e:
        history = [r for r in reviews.reviews(s) if r["suggestion"] == sid]
        return page("suggestion.html", status_code=422, who=who, s=found, history=history,
                    state=reviews.status(history), error=str(e), form=form)
    return RedirectResponse(f"/s/{quote(sid, safe='')}", status_code=303)


@app.get("/concept/{pdc:path}", response_class=HTMLResponse)
def concept(pdc: str, who: auth.Reviewer = Depends(auth.reviewer)):
    s, _ = Services.get()
    versions = reviews.concepts_at(s, pdc)
    lineages = {v["cid"] for v in versions}
    return page("concept.html", who=who, pdc=pdc, versions=versions, contested=len(lineages) > 1)
