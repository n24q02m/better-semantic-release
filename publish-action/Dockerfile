# Authoritative readback (2026-08-29):
#   docker buildx imagetools inspect python:3.13-slim-bookworm
#   index digest = sha256:c45a22ea000adfd9cda29364bbe7edd23001ce5cc2ad15857cfbf7766943b9ca
FROM python:3.13-slim-bookworm@sha256:c45a22ea000adfd9cda29364bbe7edd23001ce5cc2ad15857cfbf7766943b9ca

WORKDIR /opt/publisher

RUN addgroup --system publisher \
    && adduser --system --ingroup publisher publisher

# Copy only the stdlib runtime and its tiny action adapter. No package install,
# build tooling, source checkout, token, or dependency graph enters the image.
COPY --chown=publisher:publisher src/semantic_release/bsr/release_publisher.py /opt/publisher/release_publisher.py
COPY --chown=publisher:publisher publish-action/main.py /opt/publisher/main.py

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER publisher
ENTRYPOINT ["python", "/opt/publisher/main.py"]
