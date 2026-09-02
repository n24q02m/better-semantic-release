# Authoritative readback (2026-08-29):
#   docker buildx imagetools inspect python:3.13-slim-bookworm
#   index digest = sha256:c45a22ea000adfd9cda29364bbe7edd23001ce5cc2ad15857cfbf7766943b9ca
FROM python:3.13-slim-bookworm@sha256:c45a22ea000adfd9cda29364bbe7edd23001ce5cc2ad15857cfbf7766943b9ca

WORKDIR /opt/publisher

# The candidate workflow supplies the GitHub runner UID/GID so the mounted
# runner-owned GITHUB_OUTPUT (mode 0644) remains writable without running root.
ARG PUBLISHER_UID=1000
ARG PUBLISHER_GID=1000
RUN set -eux; \
    case "$PUBLISHER_UID" in ''|*[!0-9]*) exit 1;; esac; \
    case "$PUBLISHER_GID" in ''|*[!0-9]*) exit 1;; esac; \
    test "$PUBLISHER_UID" -ge 1; \
    test "$PUBLISHER_UID" -le 65534; \
    test "$PUBLISHER_GID" -ge 1; \
    test "$PUBLISHER_GID" -le 65534; \
    addgroup --system --gid "$PUBLISHER_GID" publisher; \
    adduser --system --uid "$PUBLISHER_UID" --ingroup publisher \
      --no-create-home --shell /usr/sbin/nologin publisher

# Copy only the stdlib runtime and its tiny action adapter. No package install,
# build tooling, source checkout, token, or dependency graph enters the image.
COPY --chown=publisher:publisher src/semantic_release/bsr/release_publisher.py /opt/publisher/release_publisher.py
COPY --chown=publisher:publisher publish-action/main.py /opt/publisher/main.py

USER publisher
ENTRYPOINT ["/usr/local/bin/python", "-I", "-S", "-B", "-u", "/opt/publisher/main.py"]
