# Production image: deps via uv, compiler fetched pinned from GitHub
# releases and baked at /opt/etal. Dev (docker compose) overrides
# ETAL_BIN to a live checkout mount instead.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl libsdl2-2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN /bin/uv sync --frozen --no-dev
COPY app ./app

ARG ETAL_VERSION=v0.1.3
ENV ETAL_SOURCE=release:${ETAL_VERSION} \
    COMPILER_DIR=/opt/etal \
    ETAL_BIN=/opt/etal/etal \
    DATABASE_URL=postgresql+psycopg://uxn:uxn@db:5432/uxnweb
COPY scripts/fetch-compiler.sh ./scripts/
COPY compiler-sha256.txt ./
RUN COMPILER_DIR=/opt/etal ./scripts/fetch-compiler.sh "release:${ETAL_VERSION}" \
    && /opt/etal/etal --list-targets

EXPOSE 8000

# Render (and most PaaS) injects $PORT and expects the process to bind
# it; --forwarded-allow-ips trusts the proxy's X-Forwarded-For, which
# is where the real client IP lives. Without it every guest would
# share one rate-limit bucket behind the proxy. One worker on purpose:
# quotas and the compile slots are in-process (see docs/operations.md).
ENV FORWARDED_ALLOW_IPS=*
CMD ["/bin/sh", "-c", "exec /srv/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS} --no-server-header"]
