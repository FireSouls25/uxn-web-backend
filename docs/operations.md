# Operations

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `ETAL_BIN` | `<checkout>/uxn-dsl/build/linux-x86/etal` | Compiler binary |
| `ETAL_VERSION` | `0.1.3` | Pinned compiler version; client mismatches get 409 |
| `DATABASE_URL` | `postgresql+psycopg://uxn:uxn@localhost:5432/uxnweb` | Postgres (sqlite URL works for tests) |
| `API_KEYS` | *(empty = open)* | Comma-separated service keys for `X-API-Key`. Empty disables service auth — dev only |
| `<PROVIDER>_API_KEY` | *(empty = excluded)* | One var per LLM provider (`GROQ_API_KEY`, `OPENAI_API_KEY`, … — see `app/llm_registry.py` for the full map). Read live from the environment on every routing decision. |
| `OLLAMA_MODEL` | *(empty = not routable)* | Model id for the local `ollama` row. Set it, then `POST /admin/llm/provider {"provider":"ollama","enabled":true}`, and a local server joins automatic routing (free tier, top weight — it answers first). |
| `LLM_DEV_BASE_URL` / `LLM_DEV_MODEL` / `LLM_DEV_KEY_ENV` | *(unset = absent)* | Escape hatch: register any OpenAI-compatible endpoint (local server, self-hosted gateway, `scripts/mock_llm.py`) as a top-ranked free candidate. `LLM_DEV_MODEL` defaults to `local`. Read live, like the keys. |
| `LLM_TIMEOUT` | `120` | Seconds per upstream call before the turn fails and the breaker trips |
| `AGENT_TURNS_PER_MINUTE` | `20` | Agent turns per user id (or guest IP); `0` disables the limit |
| `AGENT_ALLOW_GUESTS` | `1` | `0` requires an account to use the agent |
| `ETAL_SOURCE` | `local` | Compiler strategy: `local` (dev checkout) or `release:<tag>` (pinned GitHub release, hash-verified) |
| `COMPILER_DIR` | `/opt/etal` | Unpack dir for release fetches; also the fallback lookup for `etal` |
| `JWT_SECRET` | `dev-only-secret-change-me` | Signing secret. **Set a random value in production** (all tokens forgeable otherwise) |
| `ACCESS_MINUTES` / `REFRESH_DAYS` | `30` / `30` | Token lifetimes |
| `AUTH_RATE_PER_MINUTE` | `10` | Auth-endpoint budget per IP; `0` disables |
| `RATE_LIMIT_PER_MINUTE` | `30` | Compile attempts per key (or IP); `0` disables |
| `MAX_CONCURRENT_COMPILES` | `4` | Compile slots; saturation returns 503 |
| `COMPILE_TIMEOUT` | `60` | Seconds per compiler invocation |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:8000` | Browser origins |
| `LOG_LEVEL` | `INFO` | Level for the app's own loggers (`uxn.*`, `uxnweb.*`) — route decisions, provider failures, breaker trips. `WARNING` quiets the per-turn lines; they are one per turn, plus one per failure. |
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

## LLM provider keys & routing

Keys live **only on the server** and are read live from the
environment on every decision — no restarts, no redeploys:

* **Local dev:** put `GROQ_API_KEY=…` in backend `.env` (autoloaded).
* **Compose:** add it under the `api` service `environment:` (or an
  `env_file:`).
* **Production:** inject via your secrets manager (Docker secrets,
  Vault, cloud env). Never bake keys into images.
* **Rotation:** change the value where it's stored; the next routing
  decision uses it. The old value dies immediately. Nothing else
  changes.
* **Verify:** `GET /admin/llm/status` (service key) shows
  `key_present: true/false` per provider — names only, values never
  leave the server. An unset provider is silently excluded from
  routing with reason `no key (…)` in `POST /agent/route` output.

Selection is rule-based, deterministic and logged, and happens **per
turn** — there is no cached or sticky route. `llm.plan()` is the one
function that decides, and both the real turn and the operator's
preview call it:

* **filters**, each exclusion recorded with its reason: tool-calling
  support → operator kill switch → key present → breaker cooldown →
  daily token budget;
* **rank** of the survivors: free tier, then cheapest tier, then
  quality, then admin weight (ties keep registry order);
* **walk**: the head answers the turn; on a transport error, 429 or
  5xx the breaker trips and the relay tries the next candidate. A
  refused request (400) moves on too — the next provider speaks the
  same protocol. Rows without an OpenAI-compatible endpoint are
  skipped without being called, and skipping never trips a breaker;
* **outcome**: 502 when every candidate failed, 503 when none of them
  was callable, and the user sees a status, never a provider name.

Operator controls: `POST /admin/llm/provider` (enable/disable, daily
token budget). Budgets count tokens, never dollars — no invented price
table.

### Reading a decision

* **Preview it** — `POST /agent/route {}` with the service key runs the
  same `llm.plan` and returns the ranked list `/agent/turn` will walk,
  plus `excluded` with a reason per row. Health moves between a
  preview and a real turn, so the log below is the record of what
  actually answered. `GET /agent/providers` shows the same catalog
  with budgets, spend-today, fails and cooldowns.
* **Watch it happen** — two log lines per turn:
  `llm route capability=tools chosen=<provider>/<model> …` and
  `relay turn answered by <provider>/<model> (rank N of M, …)`, with
  `relay skips …` and `relay candidate N/M failed: …` along the way.
  A row in `excluded` reading `no model configured (OLLAMA_MODEL)`
  means a local server is enabled but has not been told which model to
  use. These come from the app's own loggers, which uvicorn does not
  configure: `LOG_LEVEL` (default `INFO`) is what puts them on stderr.

### Trying it without a key

`scripts/mock_llm.py` speaks the same protocol:

```sh
uv run python scripts/mock_llm.py            # http://127.0.0.1:11500/v1
LLM_DEV_BASE_URL=http://127.0.0.1:11500/v1 uv run uvicorn app.main:app
```

The dev endpoint is free tier with the top weight, so it answers
before any hosted provider — no key, no network, no spend.

### Who reaches the model

The browser only ever calls `POST /agent/turn` with a transcript and
a tool schema. It cannot name a provider, a model, or a key — the
request model refuses extra fields — so provider choice is never a
user decision and never visible in the UI.

That makes the relay a shared, metered resource, so:

* every turn is counted against `AGENT_TURNS_PER_MINUTE` (default
  20/min), keyed by user id when a token is present and by IP
  otherwise; a *present but invalid* token is rejected instead of
  silently downgraded to guest;
* set `AGENT_ALLOW_GUESTS=0` to require an account;
* per-provider daily token budgets (`POST /admin/llm/provider`) stay
  the hard ceiling, and the breaker keeps a sick provider out of the
  ranked list until its cooldown expires;
* upstream translation is OpenAI chat-completions only. Providers
  with native APIs (anthropic, google) have no `base_url` and are
  skipped rather than mistranslated — add one, or a native call
  path, before enabling them.

Logs carry the provider identity (`relay turn answered by …`,
`relay candidate failed: <p>: …`); API responses do not. If you need
to debug a route, read the logs or `GET /admin/llm/status`, not the
turn response.

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
