"""The review service (review-site/), with a local store, the fake suggestions, and a real
`pub` when PUB names one that knows the concept sort (schryer/publet#49)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "review-site"))

from review import auth, drafts, keys, main, reviews, signer, store  # noqa: E402

FIXTURES = ROOT / "review-site" / "fixtures" / "suggestions"
LED = json.loads((FIXTURES / "led-shelf.json").read_text())
QUOTED = json.loads((FIXTURES / "sandison-quoted.json").read_text())
ME = auth.Reviewer("reviewer@example.org", "dev")
ORIGIN = {"origin": "http://testserver"}


def _pub() -> str | None:
    """PUB, or the pub on PATH, if it composes a concept (schryer/publet#49)."""
    import tempfile
    pub = os.environ.get("PUB") or shutil.which("pub")
    if not pub:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        defines = Path(tmp) / "d.json"
        defines.write_text('{"sort": "concept", "pdc": "631", "name": "Agriculture in general"}')
        if subprocess.run([pub, "init"], cwd=tmp, capture_output=True).returncode != 0:
            return None
        out = subprocess.run([pub, "compose", "--class=definitional", "--content=631: Agriculture",
                              "--scope=unconditional", f"--defines={defines}"], cwd=tmp, capture_output=True)
        return pub if out.returncode == 0 else None


class FakeSigner:
    """Signs nothing: names each draft, so tests can see what would be signed."""

    def __init__(self):
        self.batches = []

    def sign(self, reviewer, ds, created):
        self.batches.append(ds)
        made = [(getattr(d, "ref", None) or f"{type(d).__name__} {getattr(d, 'target', '')}", f"pub:sha2-256:fake{i}")
                for i, d in enumerate(ds)]
        return signer.Signed("pub:sha2-256:fakekey", made, {cid: b"x" for _, cid in made})


@pytest.fixture
def local(tmp_path, monkeypatch):
    s = store.LocalStore(tmp_path / "store")
    for f in FIXTURES.glob("*.json"):
        s.create(f"suggestions/{f.name}", f.read_bytes(), "application/json")
    fake = FakeSigner()
    monkeypatch.setattr(main.Services, "store", s)
    monkeypatch.setattr(main.Services, "signer", fake)
    monkeypatch.setenv("REVIEW_DEV_USER", ME.account)
    monkeypatch.delenv("K_SERVICE", raising=False)
    return s, fake


def form(**fields) -> dict:
    return {k.replace("_", "-"): [v] for k, v in fields.items()}


def test_agree_signs_the_concept_and_one_membership_per_member_in():
    ds, framed, changes = reviews.plan(LED, "agree", {})
    composed = [d for d in ds if isinstance(d, drafts.Compose)]
    assert len(composed) == 1 and composed[0].defines["sort"] == "concept"
    assert composed[0].defines["pdc"] == "628.9~1" and composed[0].defines["kind"] == LED["candidate"]["concept"]["kind"]
    members = [d.target for d in ds if isinstance(d, drafts.Classify)]
    assert members == [m["cid"] for m in LED["candidate"]["members"] if m["in"]]
    assert changes == {}


def test_edit_records_what_changed_from_the_suggestion():
    f = form(name="LED shelf electricity", pdc="628.9~1", includes="Indoor LED shelves only",
             excludes="", member_0="a", member_1="a", member_2="out", member_3="out")
    _, framed, changes = reviews.plan(LED, "edit", f)
    assert framed[0]["members"] == [m["cid"] for m in LED["candidate"]["members"][:2]]
    assert set(changes) == {"name", "includes", "excludes", "members"}


def test_split_makes_two_concepts_and_needs_the_second_ones_members():
    f = form(name="LED shelf electricity", pdc="628.9~1", includes="LED shelves", member_0="a",
             member_1="a", member_2="out", member_3="b", name_2="Rooftop daylight growing electricity",
             pdc_2="628.9~2", includes_2="Rooftop systems under daylight")
    ds, framed, _ = reviews.plan(LED, "split", f)
    assert [c["pdc"] for c in framed] == ["628.9~1", "628.9~2"]
    assert framed[1]["members"] == [LED["candidate"]["members"][3]["cid"]]
    f["member-3"] = ["out"]
    with pytest.raises(reviews.Invalid):
        reviews.plan(LED, "split", f)


def test_not_the_same_disputes_the_suggestion_and_needs_a_reason():
    ds, _, _ = reviews.plan(LED, "reject", form(reason="The rooftop and the review measure other things."))
    assert isinstance(ds[0], drafts.Compose) and ds[0].cls == "normative"
    assert ds[1] == drafts.Relate("disputes", "reason", LED["id"])
    with pytest.raises(reviews.Invalid):
        reviews.plan(LED, "reject", form(reason="  "))


def test_not_sure_signs_nothing():
    assert reviews.plan(LED, "defer", form(note="read Sandison first"))[0] == []


def test_a_quoted_figure_is_agreed_as_derived_from_its_primary():
    ds, _, _ = reviews.plan(QUOTED, "agree", {})
    assert ds == [drafts.Relate("derived-from", QUOTED["target"], QUOTED["candidate"]["primary"])]


def test_the_pages_render_and_a_review_is_stored_with_its_assurance(local):
    s, fake = local
    client = TestClient(main.app)
    queue = client.get("/")
    assert queue.status_code == 200 and LED["title"] in queue.text and "<script" not in queue.text
    path = f"/s/{LED['id']}"
    page = client.get(path)
    assert page.status_code == 200 and "Agree and sign" in page.text and "Sandison" in page.text
    done = client.post(path, data={"action": "agree"}, headers=ORIGIN, follow_redirects=False)
    assert done.status_code == 303
    [review] = reviews.reviews(s)
    assert review["action"] == "agree" and review["assurance"] == "team login, key held by Sustenaut"
    assert "reviewer@example.org" not in json.dumps(review)  # the email stays out of the record
    assert s.list("objects/")
    assert "Agreed and signed" in client.get(path).text
    concept = client.get("/concept/628.9~1")
    assert concept.status_code == 200 and LED["candidate"]["concept"]["name"] in concept.text


def test_an_invalid_form_says_why_and_keeps_what_was_typed(local):
    client = TestClient(main.app)
    r = client.post(f"/s/{LED['id']}", data={"action": "reject", "reason": ""}, headers=ORIGIN)
    assert r.status_code == 422 and "Say why" in r.text


def test_a_post_from_another_site_is_refused(local):
    client = TestClient(main.app)
    r = client.post(f"/s/{LED['id']}", data={"action": "agree"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert reviews.reviews(local[0]) == []


def test_without_a_login_nothing_is_shown(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEW_DEV_USER", raising=False)
    monkeypatch.delenv("IAP_AUDIENCE", raising=False)
    assert TestClient(main.app).get("/").status_code == 401


def test_the_dev_login_is_refused_on_cloud_run(monkeypatch):
    monkeypatch.setenv("REVIEW_DEV_USER", ME.account)
    monkeypatch.setenv("K_SERVICE", "review-site")
    monkeypatch.delenv("IAP_AUDIENCE", raising=False)
    assert TestClient(main.app).get("/").status_code == 401


def test_the_store_never_replaces_anything(tmp_path):
    s = store.LocalStore(tmp_path)
    s.create("objects/a.cbor", b"1", "application/cbor")
    with pytest.raises(store.Exists):
        s.create("objects/a.cbor", b"2", "application/cbor")
    assert s.read("objects/a.cbor") == b"1"


def test_the_overturn_rate_counts_rejections_per_suggester(local):
    s, fake = local
    reviews.record(s, fake, ME, LED, "reject", form(reason="different systems"))
    reviews.record(s, fake, ME, QUOTED, "agree", {})
    [(engine, rate)] = reviews.overturned(s).items()
    assert rate == (1, 2)


@pytest.mark.skipif(_pub() is None, reason="needs PUB, a pub that knows the concept sort")
def test_a_held_key_signs_real_objects_and_is_reused(tmp_path):
    root = tmp_path / "keys"
    links, secrets = keys.LocalLinks(root / "links.json"), keys.LocalSecrets(root)
    held = signer.HeldKeySigner(_pub(), links, secrets)
    ds, _, _ = reviews.plan(LED, "agree", {})
    first = held.sign(ME, ds, "2026-10-14T18:00:00Z")
    assert links.key_for(ME.account) == first.key and secrets.get(first.key)
    assert first.key in first.objects                       # the new key's public object is kept
    concept = dict(first.made)["concept"]
    assert concept in first.objects
    # Each object made is signed: one sig per object, beside the objects and the key.
    assert len(first.objects) == 2 * len(first.made) + 1
    second = held.sign(ME, reviews.plan(LED, "reject", form(reason="no"))[0], "2026-10-14T18:01:00Z")
    assert second.key == first.key and first.key not in second.objects
    links.forget(ME.account)
    assert links.key_for(ME.account) is None


# --- The wallet sign-in (auth.sustenaut.eu), against a fake identity provider ---

ISSUER = "https://auth.example/realms/sustenaut"


@pytest.fixture
def idp(monkeypatch, local):
    """A fake Keycloak: its discovery, signing key and token endpoint."""
    import datetime as dt
    from urllib.parse import parse_qs as qs, urlparse

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from google.auth import crypt, jwt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    signer_ = crypt.RSASigner.from_string(pem, key_id="k1")

    monkeypatch.setenv("REVIEW_LOGIN", "wallet")
    monkeypatch.delenv("REVIEW_DEV_USER", raising=False)
    monkeypatch.setenv("KEYCLOAK_ISSUER", ISSUER)
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "review")
    monkeypatch.setenv("KEYCLOAK_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("REVIEW_SESSION_SECRET", "session-secret")
    monkeypatch.setattr(auth, "_discovery", lambda issuer: {
        "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
        "token_endpoint": f"{issuer}/protocol/openid-connect/token",
        "end_session_endpoint": f"{issuer}/protocol/openid-connect/logout",
        "jwks_uri": f"{issuer}/protocol/openid-connect/certs"})
    monkeypatch.setattr(auth, "_jwks_pems", lambda uri: {"k1": cert.public_bytes(serialization.Encoding.PEM)})

    state = {"claims": {}, "posted": None}

    class Reply:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def token_endpoint(url, data=None, timeout=None):
        state["posted"] = data
        claims = {"iss": ISSUER, "aud": "review", "sub": "kc-user-1", "name": "Mari Maasikas",
                  "iat": int(now.timestamp()), "exp": int(now.timestamp()) + 300, **state["claims"]}
        return Reply({"id_token": jwt.encode(signer_, claims).decode()})

    monkeypatch.setattr("requests.post", token_endpoint)

    def sign_in(client, next_path="/"):
        """Follow /login to the fake Keycloak and back; returns the callback's response."""
        r = client.get("/login", params={"next": next_path}, follow_redirects=False)
        params = {k: v[0] for k, v in qs(urlparse(r.headers["location"]).query).items()}
        state["claims"].setdefault("nonce", params["nonce"])
        return client.get("/auth/callback", params={"code": "c0de", "state": params["state"]},
                          follow_redirects=False), params

    state["sign_in"] = sign_in
    return state


def test_login_sends_the_browser_to_keycloak_with_pkce(idp):
    from urllib.parse import parse_qs as qs, urlparse
    client = TestClient(main.app)
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303
    url = urlparse(r.headers["location"])
    params = {k: v[0] for k, v in qs(url.query).items()}
    assert url.geturl().startswith(f"{ISSUER}/protocol/openid-connect/auth")
    assert params["client_id"] == "review" and params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == "http://testserver/auth/callback"
    assert params["state"] and params["nonce"] and len(params["code_challenge"]) == 43


def test_a_wallet_sign_in_starts_a_session_and_the_pages_show_it(idp):
    client = TestClient(main.app)
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/login?next=")
    r, params = idp["sign_in"](client)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert idp["posted"]["code_verifier"] and idp["posted"]["client_secret"] == "s3cret"
    page_ = client.get("/")
    assert page_.status_code == 200
    assert "Mari Maasikas" in page_.text and "EU wallet sign-in, key held by Sustenaut" in page_.text
    assert "Sign out" in page_.text


def test_a_wallet_reviewers_key_is_linked_to_the_wallet_account(idp, local):
    s, fake = local
    client = TestClient(main.app)
    idp["sign_in"](client)
    seen = []
    fake.sign = lambda reviewer, ds, created: (seen.append(reviewer), FakeSigner.sign(fake, reviewer, ds, created))[1]
    r = client.post(f"/s/{LED['id']}", data={"action": "agree"}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code in (200, 303), r.text
    assert seen and seen[0].account == "wallet:kc-user-1" and seen[0].login == "wallet"
    review = json.loads(s.read(sorted(n for n in s.list("reviews/"))[-1]))
    assert review["login"] == "wallet" and review["assurance"] == "EU wallet sign-in, key held by Sustenaut"
    assert "Mari" not in json.dumps(review)  # the name stays out of the record


def test_a_tampered_session_is_not_a_sign_in(idp):
    client = TestClient(main.app)
    idp["sign_in"](client)
    client.cookies.set(auth.SESSION_COOKIE, client.cookies.get(auth.SESSION_COOKIE)[:-2] + "xx")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_an_id_token_for_another_sign_in_is_refused(idp):
    idp["claims"]["nonce"] = "someone-elses"
    r, _ = idp["sign_in"](TestClient(main.app))
    assert r.status_code == 401 and auth.SESSION_COOKIE not in r.cookies


def test_with_iap_and_wallet_iap_still_decides_who_gets_in(idp, monkeypatch):
    monkeypatch.setenv("REVIEW_LOGIN", "iap+wallet")
    client = TestClient(main.app)
    idp["sign_in"](client)
    assert client.get("/", follow_redirects=False).status_code == 401  # no IAP header: no entry


def test_sign_out_ends_the_session_here_and_at_keycloak(idp):
    from urllib.parse import parse_qs as qs, urlparse
    client = TestClient(main.app)
    idp["sign_in"](client)
    r = client.get("/logout", follow_redirects=False)
    params = {k: v[0] for k, v in qs(urlparse(r.headers["location"]).query).items()}
    assert r.headers["location"].startswith(f"{ISSUER}/protocol/openid-connect/logout")
    assert params["id_token_hint"] and params["post_logout_redirect_uri"] == "http://testserver/"
    assert client.get("/", follow_redirects=False).status_code == 303  # signed out here too
