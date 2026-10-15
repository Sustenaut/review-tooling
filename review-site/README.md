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

- **Who:** the IAP-verified Google account on the team list (`review/auth.py`).
- **Key:** the service holds one key per reviewer, made the first time
  they sign (`review/signer.py`, through the `pub` CLI). The key's secret
  is in its own bucket. The link from the account to the key is in its own
  Firestore collection, `review_key_links`, which can be deleted without
  touching any publet. Publets never carry the email.
- **Assurance:** every review is marked **"team login, key held by
  Sustenaut"**.

The shared sign-in (Keycloak, wallet-first) is built in its own thread. It
replaces `auth.reviewer` and `signer.HeldKeySigner`, behind the same
`Reviewer` and `Signer` interfaces. A wallet-signed attestation can then
vouch for a person's own key.

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

`infra/review-site.tf` holds the service, its image repository, the
append-only bucket, the keys bucket, the service account and the domain
mapping. After `./with-secrets terraform -chdir=infra apply`, run
`./with-secrets infra/iap-oauth.sh` to give its login the OAuth client. The
`review` DNS record goes in schryer/schryer-eu-infra. Then
`.github/workflows/review-site.yml` builds, checks and deploys the image on
each merge to main that touches the service, or when run by hand
(`gh workflow run "review site"`). The image builds `pub` from publet at a
pinned commit (`Dockerfile`, `PUBLET_REV`).
