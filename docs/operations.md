# Operations

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `ETAL_BIN` | `<checkout>/uxn-dsl/build/linux-x86/etal` | Compiler binary |
| `ETAL_VERSION` | `0.1.3` | Pinned compiler version; client mismatches get 409 |
| `DATABASE_URL` | `postgresql+psycopg://uxn:uxn@localhost:5432/uxnweb` | Postgres (sqlite URL works for tests) |
| `API_KEYS` | *(empty = open)* | Comma-separated service keys for `X-API-Key`. Empty disables service auth — dev only |
| `ETAL_SOURCE` | `local` | Compiler strategy: `local` (dev checkout) or `release:<tag>` (pinned GitHub release, hash-verified) |
| `COMPILER_DIR` | `/opt/etal` | Unpack dir for release fetches; also the fallback lookup for `etal` |
| `JWT_SECRET` | `dev-only-secret-change-me` | Signing secret. **Set a random value in production** (all tokens forgeable otherwise) |
| `ACCESS_MINUTES` / `REFRESH_DAYS` | `30` / `30` | Token lifetimes |
| `AUTH_RATE_PER_MINUTE` | `10` | Auth-endpoint budget per IP; `0` disables |
| `RATE_LIMIT_PER_MINUTE` | `30` | Compile attempts per key (or IP); `0` disables |
| `MAX_CONCURRENT_COMPILES` | `4` | Compile slots; saturation returns 503 |
| `COMPILE_TIMEOUT` | `60` | Seconds per compiler invocation |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:8000` | Browser origins |
| `MAX_FILES` / `MAX_FILE_BYTES` / `MAX_TOTAL_BYTES` / `MAX_PATH_CHARS` | `64` / 256KB / 2MB / 64 | Upload caps |

## Running

```sh
cp .env.example .env
uv sync
uv run uvicorn app.main:app --port 8000      # single worker (see below)
docker compose up --build                     # API + Postgres 16
```

## Compiler provisioning

No checkout required. Resolution order: `ETAL_BIN` → `$COMPILER_DIR/etal`
→ auto-fetch when `ETAL_SOURCE=release:<tag>`:

```sh
ETAL_SOURCE=release:v0.1.3 COMPILER_DIR=/tmp/etal ./scripts/fetch-compiler.sh
```

Release tarballs come from the compiler repo's GitHub releases and are
**refused unless their sha256 is pinned** in `compiler-sha256.txt`
(add a row per upgrade). The Docker image bakes `release:vX` at build
time (`ARG ETAL_VERSION`); compose uses the same baked binary (a
host-checkout mount does not work — see gotchas). `/health` reports `compiler: ok` or
the guidance string when nothing resolves.

Two container gotchas, both bitten once:
* Never mount a host-built `etal` into the slim image — it needs a
  newer glibc than the image provides. The baked release binary is
  the only compiler compose uses.
* The image must install `libsdl2-2.0-0`: the vendored `uxn2` VM links
  SDL2 dynamically and assembly (via drifblim) runs inside it, even
  headless.

## Security model

* The compiler runs as a subprocess in a fresh tempdir per job
  (removed on all paths), with no network, a wall-clock timeout, and
  strict path/extension/size validation before anything touches disk.
* Auth is a shared-secret header — enough for a first-party frontend
  behind TLS, not for untrusted third parties. Real multi-tenant auth
  is deferred (see root `ARCHITECTURE.md` v2).
* Rate limits and compile slots are **in-memory and per-worker**.
  Run exactly one uvicorn worker until a shared store (Redis) lands;
  `--workers 2+` would multiply every quota.
* Every response carries `nosniff` / `DENY` framing /
  `no-referrer` headers. Terminate TLS in front (reverse proxy);
  never expose uvicorn directly.

## Upgrades (0.x)

Tables are created with `create_all` — there are no migrations yet.
A schema change (e.g. a new `compile_jobs` column) requires a fresh
database: `docker compose down -v` recreates `pgdata`. Alembic before
any production data matters.
