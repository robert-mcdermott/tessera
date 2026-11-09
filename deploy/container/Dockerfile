# Tessera as a container image.
#
#   podman build -f deploy/container/Dockerfile -t tessera:1.0.0 .     (or docker build)
#
# Built with uv from the lock file, so the image has exactly the tested versions. It runs as
# an unprivileged user; the configuration and the data home are volumes, so the image itself
# holds no state and is replaced at each upgrade. See docs/operations.md, "Running it as a
# service".

FROM docker.io/library/python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/tessera
WORKDIR /build
# Dependencies first, so a change to Tessera alone does not reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra postgres --no-editable

FROM docker.io/library/python:3.13-slim
LABEL org.opencontainers.image.title="Tessera" \
      org.opencontainers.image.description="Research intelligence from public sources, with the evidence behind every connection" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/robert-mcdermott/tessera"
RUN groupadd --system --gid 10001 tessera \
 && useradd --system --uid 10001 --gid tessera --home-dir /srv/tessera --shell /usr/sbin/nologin tessera \
 && mkdir -p /srv/tessera/config /srv/tessera/data \
 && chown -R tessera:tessera /srv/tessera
COPY --from=build /opt/tessera /opt/tessera
# Four server processes, so one slow request never holds up the rest (each takes its own
# memory: about 0.7 GB at 100,000 publications). Change with -e TESSERA_SERVER_WORKERS=…
ENV PATH=/opt/tessera/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    TESSERA_CONFIG=/srv/tessera/config/tessera.toml \
    TESSERA_DATA_HOME=/srv/tessera/data \
    TESSERA_SERVER_WORKERS=4
USER tessera
WORKDIR /srv/tessera
VOLUME ["/srv/tessera/config", "/srv/tessera/data"]
EXPOSE 8000
# Alive: the process answers. (Ready, for a load balancer: /api/v1/health/ready.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=4).status == 200 else 1)"]
# Every interface inside the container: publish the port only to a reverse proxy, and turn on
# accounts ([server] accounts = true), without which Tessera refuses to listen beyond this machine.
CMD ["tessera", "serve", "--host", "0.0.0.0", "--port", "8000"]
