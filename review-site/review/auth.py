"""Who is reviewing.

Two logins, set by REVIEW_LOGIN:

- ``iap`` (the default): the service sits behind Google's Identity-Aware Proxy
  (IAP) like the planning app and pub.sustenaut.eu. IAP signs each request with
  a JWT; the service checks that signature and that the email is on the team
  list, so a request that skips IAP gets nothing.
- ``wallet``: the shared sign-in at auth.sustenaut.eu (Keycloak, EU Digital
  Identity Wallet only). The service is a standard OpenID Connect client
  (client ``review``, code flow with PKCE): /login sends the browser there,
  /auth/callback checks the ID token Keycloak signs, and a signed session
  cookie remembers the account.
- ``iap+wallet``: both, while the team has no wallets yet. IAP still decides
  who reaches the service at all; a team member may then sign in with a wallet,
  and reviews they sign from then on are the wallet account's.

Everything else asks only for a `Reviewer`, so the login touches this file and
nothing more. Locally, set REVIEW_DEV_USER to your email instead; it is refused
on Cloud Run.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

log = logging.getLogger("uvicorn.error")

IAP_CERTS = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"

# How sure a reader can be of who signed, by login. The service, not the
# person, holds the key in both cases; a key of the person's own, vouched for
# by a wallet-signed statement, replaces that later.
ASSURANCE = {
    "iap": "team login, key held by Sustenaut",
    "dev": "team login, key held by Sustenaut",
    "wallet": "EU wallet sign-in, key held by Sustenaut",
}

SESSION_COOKIE = "review_session"
FLOW_COOKIE = "review_signin"
SESSION_HOURS = 12


@dataclass(frozen=True)
class Reviewer:
    """A signed-in person, as the login vouches for them."""

    account: str  # what their review key is linked to: the email, or wallet:<Keycloak id>
    login: str  # "iap", "wallet" or "dev"
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.account

    @property
    def assurance(self) -> str:
        return ASSURANCE[self.login]


def login_mode() -> str:
    mode = os.environ.get("REVIEW_LOGIN", "iap")
    if mode not in ("iap", "wallet", "iap+wallet"):
        raise RuntimeError(f"REVIEW_LOGIN is iap, wallet or iap+wallet, not {mode!r}")
    return mode


def team() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("TEAM", "").split(",") if e.strip()}


# --- IAP ---------------------------------------------------------------------

def _verify(token: str, audience: str) -> dict:
    """The token's claims once its signature, expiry, issuer and audience check out."""
    from google.auth.transport import requests
    from google.oauth2 import id_token

    claims = id_token.verify_token(token, requests.Request(), audience=None, certs_url=IAP_CERTS)
    if claims.get("iss") != IAP_ISSUER:
        raise ValueError(f"issued by {claims.get('iss')!r}, not IAP")
    if claims.get("aud") != audience:
        raise ValueError(f"audience {claims.get('aud')!r}, expected {audience!r} (IAP_AUDIENCE)")
    return claims


def _iap_reviewer(request: Request) -> Reviewer:
    token = request.headers.get("x-goog-iap-jwt-assertion")
    audience = os.environ.get("IAP_AUDIENCE")
    if not token or not audience:
        raise HTTPException(401, "Sign in through https://review.sustenaut.eu")
    try:
        email = _verify(token, audience).get("email", "").lower()
    except Exception as e:
        log.warning("IAP sign-in rejected: %s: %s", type(e).__name__, e)
        raise HTTPException(401, f"The sign-in couldn't be checked: {e}")
    if email not in team():
        raise HTTPException(403, f"{email} isn't on the team list (infra/variables.tf, var.team)")
    return Reviewer(email, "iap")


# --- The wallet sign-in (OpenID Connect, auth.sustenaut.eu) -------------------

SETTINGS = ("KEYCLOAK_ISSUER", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET", "REVIEW_SESSION_SECRET")


def _settings() -> dict:
    missing = [n for n in SETTINGS if not os.environ.get(n)]
    if missing:
        raise HTTPException(500, f"The wallet sign-in is not set up: {', '.join(missing)} missing")
    return {n: os.environ[n] for n in SETTINGS}


def _serializer(purpose: str):
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(_settings()["REVIEW_SESSION_SECRET"], salt=f"review-{purpose}")


@lru_cache(maxsize=4)
def _discovery(issuer: str) -> dict:
    import requests

    return requests.get(f"{issuer}/.well-known/openid-configuration", timeout=10).json()


def _jwks_pems(jwks_uri: str) -> dict[str, bytes]:
    """The issuer's signing certificates by key id, as PEM, from its JWKS (x5c)."""
    import requests

    out = {}
    for key in requests.get(jwks_uri, timeout=10).json().get("keys", []):
        if key.get("use", "sig") == "sig" and key.get("x5c"):
            body = key["x5c"][0]
            lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
            out[key["kid"]] = f"-----BEGIN CERTIFICATE-----\n{lines}\n-----END CERTIFICATE-----\n".encode()
    return out


def verify_id_token(token: str, issuer: str, client_id: str, nonce: str) -> dict:
    """The ID token's claims once its signature (by the issuer's published key), expiry,
    issuer, audience and nonce check out."""
    from google.auth import jwt

    claims = jwt.decode(token, certs=_jwks_pems(_discovery(issuer)["jwks_uri"]), audience=client_id)
    if claims.get("iss") != issuer:
        raise ValueError(f"issued by {claims.get('iss')!r}, not {issuer!r}")
    if claims.get("nonce") != nonce:
        raise ValueError("the sign-in's nonce does not match")
    return claims


def _public_base(request: Request) -> str:
    return os.environ.get("REVIEW_PUBLIC_URL", str(request.base_url)).rstrip("/")


def _wallet_reviewer(request: Request) -> Reviewer | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    from itsdangerous import BadSignature

    try:
        session = _serializer("session").loads(raw, max_age=SESSION_HOURS * 3600)
    except BadSignature:
        return None
    return Reviewer(f"wallet:{session['sub']}", "wallet", session.get("name", ""))


def _to_login(request: Request) -> HTTPException:
    next_path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return HTTPException(303, "Sign in with your wallet",
                         headers={"Location": "/login?" + urllib.parse.urlencode({"next": next_path})})


def reviewer(request: Request) -> Reviewer:
    """The signed-in reviewer; 401 or 403 (or, for the wallet login, a redirect) otherwise."""
    dev = os.environ.get("REVIEW_DEV_USER")
    if dev and not os.environ.get("K_SERVICE"):
        return Reviewer(dev.lower(), "dev")
    mode = login_mode()
    if mode == "iap":
        return _iap_reviewer(request)
    if mode == "iap+wallet":
        team_member = _iap_reviewer(request)  # IAP still decides who gets in at all
        return _wallet_reviewer(request) or team_member
    found = _wallet_reviewer(request)
    if not found:
        raise _to_login(request)
    return found


def wallet_offered() -> bool:
    """Whether pages offer the wallet sign-in (and sign-out)."""
    return login_mode() != "iap"


def _safe_next(value: str | None) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") else "/"


router = APIRouter()


@router.get("/login")
def login(request: Request, next: str | None = None):
    """Send the browser to auth.sustenaut.eu, remembering this sign-in in a short-lived cookie."""
    if not wallet_offered():
        raise HTTPException(404)
    s = _settings()
    verifier = secrets.token_urlsafe(48)
    flow = {"state": secrets.token_urlsafe(16), "nonce": secrets.token_urlsafe(16),
            "verifier": verifier, "next": _safe_next(next)}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "client_id": s["KEYCLOAK_CLIENT_ID"], "response_type": "code", "scope": "openid profile",
        "redirect_uri": f"{_public_base(request)}/auth/callback", "state": flow["state"],
        "nonce": flow["nonce"], "code_challenge": challenge, "code_challenge_method": "S256",
    }
    response = RedirectResponse(_discovery(s["KEYCLOAK_ISSUER"])["authorization_endpoint"] + "?"
                                + urllib.parse.urlencode(params), status_code=303)
    response.set_cookie(FLOW_COOKIE, _serializer("flow").dumps(flow), max_age=600, httponly=True,
                        secure=request.url.scheme == "https", samesite="lax", path="/auth/callback")
    return response


@router.get("/auth/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    """Back from auth.sustenaut.eu: exchange the code, check the ID token, start a session."""
    import requests
    from itsdangerous import BadSignature

    if error:
        raise HTTPException(401, f"The wallet sign-in did not complete: {error}")
    s = _settings()
    try:
        flow = _serializer("flow").loads(request.cookies.get(FLOW_COOKIE, ""), max_age=600)
    except BadSignature:
        raise HTTPException(400, "The sign-in expired or came from somewhere else; start again at /login")
    if not code or state != flow["state"]:
        raise HTTPException(400, "The sign-in's state does not match; start again at /login")
    issuer = s["KEYCLOAK_ISSUER"]
    reply = requests.post(_discovery(issuer)["token_endpoint"], timeout=10, data={
        "grant_type": "authorization_code", "code": code, "code_verifier": flow["verifier"],
        "redirect_uri": f"{_public_base(request)}/auth/callback",
        "client_id": s["KEYCLOAK_CLIENT_ID"], "client_secret": s["KEYCLOAK_CLIENT_SECRET"],
    })
    if reply.status_code != 200:
        log.warning("Token exchange failed: %s %s", reply.status_code, reply.text[:200])
        raise HTTPException(401, "auth.sustenaut.eu did not accept the sign-in")
    id_token = reply.json().get("id_token", "")
    try:
        claims = verify_id_token(id_token, issuer, s["KEYCLOAK_CLIENT_ID"], flow["nonce"])
    except Exception as e:
        log.warning("ID token rejected: %s: %s", type(e).__name__, e)
        raise HTTPException(401, f"The sign-in couldn't be checked: {e}")
    # The ID token goes with the session only so sign-out can name it (id_token_hint):
    # Keycloak then ends its own session too, without asking.
    session = {"sub": claims["sub"], "name": claims.get("name", ""), "at": int(time.time()), "idt": id_token}
    response = RedirectResponse(flow["next"], status_code=303)
    response.delete_cookie(FLOW_COOKIE, path="/auth/callback")
    response.set_cookie(SESSION_COOKIE, _serializer("session").dumps(session), max_age=SESSION_HOURS * 3600,
                        httponly=True, secure=request.url.scheme == "https", samesite="lax")
    return response


@router.get("/logout")
def logout(request: Request):
    """Forget the wallet session here, then at auth.sustenaut.eu (single sign-out)."""
    if not wallet_offered():
        raise HTTPException(404)
    s = _settings()
    params = {"client_id": s["KEYCLOAK_CLIENT_ID"], "post_logout_redirect_uri": f"{_public_base(request)}/"}
    from itsdangerous import BadSignature

    try:
        params["id_token_hint"] = _serializer("session").loads(request.cookies.get(SESSION_COOKIE, ""))["idt"]
    except (BadSignature, KeyError):
        pass
    response = RedirectResponse(_discovery(s["KEYCLOAK_ISSUER"])["end_session_endpoint"] + "?"
                                + urllib.parse.urlencode(params), status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


def same_origin(request: Request) -> None:
    """A form post must come from this service's own pages, so another site can't make a
    signed-in browser sign something. Forms have no scripts to add a header, so this
    checks the browser's Origin (or, without one, Referer) against the request's host."""
    host = request.headers.get("host", "")
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if not host or "://" not in origin or origin.split("://", 1)[1].split("/", 1)[0] != host:
        raise HTTPException(403, "Reviews are only accepted from the review service's own pages")
