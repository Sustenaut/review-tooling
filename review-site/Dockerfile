# The review service (review.sustenaut.eu). Build from the repo root:
#   podman build -f review-site/Dockerfile -t review-site .
# Cloud Run supplies PORT; IAP in front of the service handles the login and
# the service checks its signature (review-site/review/auth.py).

# pub signs reviews and bundles publication 0 and the paper extracts for
# names. It is the publet-cli release the extracts were signed with
# (FACTS_PUBLET_TAG in the Makefile), which carries the concept sort.
FROM docker.io/library/rust:1-slim AS pub
ARG PUBLET_REV=v0.2.2
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/Sustenaut/publet.git /publet \
    && git -C /publet fetch --quiet origin "${PUBLET_REV}" \
    && git -C /publet checkout --quiet "${PUBLET_REV}"
WORKDIR /publet
RUN cargo build --release --locked -p publet-cli

# The paper extracts and what they cite, from storage.sustenaut.eu, so a
# suggestion's members and a signed concept's members show by name.
FROM python:3.14-slim AS sources
COPY corpus/sources/gathered.json /gathered.json
# With a User-Agent of its own: Cloudflare refuses Python's default (error 1010).
RUN mkdir /sources && python -c 'import json, urllib.request; \
    opener = urllib.request.build_opener(); opener.addheaders = [("User-Agent", "sustenaut-review-site-build")]; \
    urllib.request.install_opener(opener); \
    [urllib.request.urlretrieve(u, "/sources/" + n) for n, u in json.load(open("/gathered.json"))["objects"].items() if u]'

FROM docker.io/library/debian:stable-slim AS bundles
COPY --from=pub /publet/target/release/pub /usr/local/bin/pub
COPY publication-0/objects /objects
COPY --from=sources /sources /objects
RUN pub bundle --dir=/objects --out=/bundles --base=https://pub.sustenaut.eu

FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080 \
    PUB=/usr/local/bin/pub REVIEW_BUNDLES=/app/bundles
WORKDIR /app
COPY requirements.lock review-site/requirements.txt /tmp/
RUN pip install -r /tmp/requirements.txt -c /tmp/requirements.lock
COPY --from=pub /publet/target/release/pub /usr/local/bin/pub
RUN useradd --create-home --uid 1000 app
COPY --chown=app --from=bundles /bundles bundles/
COPY --chown=app review-site/review/ review/
USER app
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn review.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
