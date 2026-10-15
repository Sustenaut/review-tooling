# The review service (review.sustenaut.eu). Build from the repo root:
#   podman build -f review-site/Dockerfile -t review-site .
# Cloud Run supplies PORT; IAP in front of the service handles the login and
# the service checks its signature (review-site/review/auth.py).

# pub signs reviews and bundles publication 0 for names. It is pinned by
# commit: schryer/publet main after #49 merged, which adds the concept sort.
# Move it to a release tag once one carries the concept sort.
FROM docker.io/library/rust:1-slim AS pub
ARG PUBLET_REV=a48d3639063e8cb732292f5025d84be83d1b59f5
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/schryer/publet.git /publet \
    && git -C /publet fetch --quiet origin "${PUBLET_REV}" \
    && git -C /publet checkout --quiet "${PUBLET_REV}"
WORKDIR /publet
RUN cargo build --release --locked -p publet-cli

FROM docker.io/library/debian:stable-slim AS bundles
COPY --from=pub /publet/target/release/pub /usr/local/bin/pub
COPY publication-0/objects /objects
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
