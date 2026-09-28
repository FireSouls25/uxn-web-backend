# Local development (uv + mise).
cp .env.example .env   # then point ETAL_BIN at your checkout if needed
uv sync
uv run pytest
uv run uvicorn app.main:app --reload --port 8000

# Docker (API + Postgres). The compiler checkout mounts read-only;
# production images should bake it in instead (see Dockerfile).
docker compose up --build

Open http://localhost:8000/docs for the interactive API.
Full references live in `docs/`: `api.md` (endpoints, codes, i18n),
`operations.md` (env vars, docker, security model, upgrades).
