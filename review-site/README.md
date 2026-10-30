# review-site

Reviews of machine suggestions about publets, at
[review.sustenaut.eu](https://review.sustenaut.eu): a separate Cloud Run
service, deployed and scaled on its own, behind the same Google login (IAP)
as the other apps for now. This is piece 3 of the reference review design
(draft 4, decided 2026-10-14).

A suggester proposes which extract claims measure one concept, or which
figure is quoted from another. Each suggestion is a `triage` annotation
(publet spec Section 7.4) with a `candidate` blob of unsigned drafts. A
person opens it, edits it in place, and signs. **Only what a person signs
is part of the record.**

| Page | What it is |
|---|---|
| `/` | The queue: open suggestions first, and how often each suggester is overturned. |
| `/s/<id>` | One suggestion: members, the concept's name, PDC code, kind and boundary lines, and the actions. |
| `/concept/<pdc>` | Every concept signed at a PDC code, newest first. A code with more than one is shown as contested. |

| Action | What gets signed |
|---|---|
| Agree and sign | The concept as suggested (`definitional`, `defines.sort: "concept"`), and one `classifies` per member. |
| Edit and sign | The same, as the form stands. The review records what changed. |
| Split | Two concepts, each with its members. |
| Not the same | A claim stating why, and a `disputes` relation from it against the suggestion. |
| Not sure yet | Nothing. A note is kept and the suggestion stays in the queue. |
| Agree (quoted figure) | A `derived-from` relation from the quoting extract to the primary one. |

Pages are server-rendered HTML forms, with no JavaScript. A form post must
come from the service's own pages (its `Origin` is checked).

## Identity and signing, for now

`REVIEW_LOGIN` picks the login (`review/auth.py`):

- `iap`: the IAP-verified Google account on the team list.
- `wallet`: the shared sign-in at auth.sustenaut.eu (Keycloak, EU Digital
  Identity Wallet only), as the OpenID Connect client `review`: `/login` sends
  the browser there (code flow with PKCE), `/auth/callback` checks the ID token
  Keycloak signs, and a signed session cookie keeps the account for 12 hours.
  `/logout` ends that session and Keycloak's (single sign-out).
- `iap+wallet` (what review.sustenaut.eu runs now): IAP still decides who gets
  in at all; a team member may then sign in with a wallet, and reviews they sign
  from then on are the wallet account's. When wallets are issued, the setting
  becomes `wallet` and IAP comes off.

- **Key:** the service holds one key per reviewer, made the first time they
  sign (`review/signer.py`, through the `pub` CLI). The key's secret is in its
  own bucket. The link from the account (the email, or `wallet:<Keycloak id>`)
  to the key is in its own Firestore collection, `review_key_links`, which can
  be deleted without touching any publet. Publets never carry the email or the
  name.
- **Assurance:** every review records how its signer signed in: **"team login,
  key held by Sustenaut"** or **"EU wallet sign-in, key held by Sustenaut"**.

A wallet-signed attestation can later vouch for a person's own key, replacing
the held key behind the same `Signer` interface.

## Storage

The bucket only ever grows. The service may create objects and read them,
never replace or delete one:

    suggestions/<id>.json      written by the suggestion job (a later piece)
    objects/<cid>.cbor         signed objects: concepts, memberships, relations, sigs, keys
    reviews/<when>-<id>-<action>.json   what a reviewer did, the objects it made, what changed

The pub site's build can read `objects/` alongside publication 0's
committed objects.

## Running it locally

```sh
make review        # http://localhost:8001, with fixtures/suggestions/ in build/review-store
```

`REVIEW_DEV_USER` stands in for the login, and `PUB` must name a `pub`
that knows the `concept` sort (schryer/publet#49). The suggestions in
`fixtures/` are fake.

## Deploying

The service, its image repository, the buckets and the domain mapping are in
Sustenaut/business-plan's `infra/review-site.tf`, and business-plan also builds
and deploys the image. The image needs publication 0's objects and the paper
extracts, which are private until published, and they change more often than
this code. So the build stays beside that data: its `review-site/Dockerfile`
clones this repository at a pinned tag and copies `review-site/review/` in.
Nothing about the data reaches this repository.
