# Production image: deps via uv, compiler baked in at /opt/uxn-dsl.
# Dev (docker compose) mounts the checkout read-only instead; both
# paths work because ETAL_BIN is just an env var.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN /bin/uv sync --frozen --no-dev
COPY app ./app

ENV ETAL_BIN=/opt/uxn-dsl/build/linux-x86/etal \
    DATABASE_URL=postgresql+psycopg://uxn:uxn@db:5432/uxnweb

EXPOSE 8000
CMD ["/srv/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
