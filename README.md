# Local development (uv + mise).
uv sync
uv run pytest
uv run uvicorn app.main:app --reload --port 8000
# No env needed locally: backend/.env supplies dev defaults
# (see .env.example). Real environment always wins over .env.

# Docker (API + Postgres). The compiler checkout mounts read-only;
# production images should bake it in instead (see Dockerfile).
docker compose up --build

Open http://localhost:8000/docs for the interactive API.
Full references live in `docs/`: `api.md` (endpoints, codes, i18n),
`operations.md` (env vars, docker, security model, upgrades),
`deploy.md` (Render + Vercel, step by step).
