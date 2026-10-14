"""Who is reviewing.

For now the review service sits behind Google's Identity-Aware Proxy (IAP)
like the planning app and pub.sustenaut.eu: IAP signs each request with a
JWT, and the service checks that signature and that the email is on the
team list, so a request that skips IAP gets nothing. The shared sign-in
(Keycloak, wallet-first) replaces this later; everything else asks only for
a `Reviewer`, so that move touches this file and the signer, nothing more.

Locally, set REVIEW_DEV_USER to your email instead; it is refused on Cloud
Run.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from fastapi import HTTPException, Request

log = logging.getLogger("uvicorn.error")

IAP_CERTS = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"

# How sure a reader can be of who signed: the login vouches for the
# account, and the service, not the person, holds the key. The wallet
# login and a key of the person's own replace both.
ASSURANCE = "team login, key held by Sustenaut"


@dataclass(frozen=True)
class Reviewer:
    """A signed-in person, as the login vouches for them."""

    email: str
    login: str  # "iap" or "dev"


def team() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("TEAM", "").split(",") if e.strip()}


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


def reviewer(request: Request) -> Reviewer:
    """The signed-in reviewer; 401 or 403 otherwise."""
    dev = os.environ.get("REVIEW_DEV_USER")
    if dev and not os.environ.get("K_SERVICE"):
        return Reviewer(dev.lower(), "dev")
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


def same_origin(request: Request) -> None:
    """A form post must come from this service's own pages, so another site can't make a
    signed-in browser sign something. Forms have no scripts to add a header, so this
    checks the browser's Origin (or, without one, Referer) against the request's host."""
    host = request.headers.get("host", "")
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if not host or "://" not in origin or origin.split("://", 1)[1].split("/", 1)[0] != host:
        raise HTTPException(403, "Reviews are only accepted from the review service's own pages")
