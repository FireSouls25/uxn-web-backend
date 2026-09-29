# Backend — architecture and features

Thin compile service over the `etal` compiler. The frontend owns the
low-code editor (`Project JSON -> .ux`); this service receives a file
tree, runs the pinned compiler, and returns build artifacts.

## Toolchain contract (pinned)

* Compiler: `etal` from the `uxn-dsl` repo, pinned via `flake.lock`
  (Nix) or a vendored binary + `vendor/` tree. Never `latest`.
* Modes used: `-t` (inspect), `-r` (ROM), default bundle with
  `--target web | native | <explicit row>`.
* Discovery: `etal --list-targets` (TSV `target/kind/vendored`) backs
  `GET /targets`, so the frontend dropdown always matches what this
  backend can actually serve.
* Assembly always executes under the host VM; `--target` only selects
  the packaged VM. One Linux host therefore serves `rom`, `web`,
  `linux-*` and (vendored) `macos-*` rows. `windows-x86_64` stays
  unserved until `vendor/windows-x86_64/uxn2.exe` lands upstream —
  the compiler already fails loudly naming that path.
* Diagnostics: `etal: error: <file>:<line>:<col>: <msg>` on stderr
  plus the offending source line. Machine-parseable; parse, don't
  regex loosely.

## API (v1)

* `GET /health` — liveness.
* `GET /version` — `{ etal, vendor_rows, backend }`. The frontend
  sends its expected `etal` version with every compile; mismatch is
  `409`, never a silent miscompile.
* `GET /targets` — rows + `vendored` flags, straight from
  `--list-targets`.
* `POST /compile` — `{ target, entry, files: {path: content}, etal_version }`
  → `{ status, artifacts: {tal?, rom_b64?, html?, bundle_b64?},
  diagnostics: [{file, line, col, msg}] }`.
  `files` keys must be relative, no `..`, no absolute paths
  (ETAL `import`/`file()` resolve file-relative; drifblim path
  buffers are ~63B, so keep names short and bare).

## Agent routing (`/agent/*`)

The frontend runs the agent *loop*; this service owns the *model*.
`POST /agent/turn` takes a transcript and a tool schema, picks a
route, calls the provider with a server-held key and returns the
reply. The client cannot name a provider, a model or a key — the
request model forbids extra fields, so a crafted body cannot smuggle
a route in.

* `llm_registry.py` — what exists: base URL, key env var, cost tier,
  quality, admin weight, tool-calling support. Static rows, plus
  dynamic ones (a local model server) whose model id comes from the
  environment on every call.
* `llm.py` — the decision. `llm.plan()` filters (tool support →
  enabled → key → cooldown → budget), ranks what survives (free →
  tier → quality → weight) and logs the head. Every exclusion carries
  a reason, so the catalog can always explain itself. Same function
  answers the operator preview (`POST /agent/route`) and the real
  turn, so a preview cannot lie.
* `llm_relay.py` — the walk. The head answers; on failure the next
  candidate does, tripping the breaker on transport errors, 429 and
  5xx. Rows with no OpenAI-compatible endpoint are skipped, not
  mistranslated. Usage lands in a token ledger (never dollars).
* Budgets, kill switches and cooldowns are per provider and live in
  Postgres; keys stay in the environment. Provider identity appears in
  logs and service endpoints, never in a turn response.

Because routing is a shared, metered resource, the browser side also
carries a per-caller turn rate limit and an optional
`AGENT_ALLOW_GUESTS=0` switch. `docs/operations.md` has the key and
log story; `rag/agent-contract.md` has the wire contract.

## Execution model

1. Validate request (paths, total size cap, extension allowlist:
   `.ux`, `.chr`, `.icn`, `.wav`).
2. Materialize the tree into a fresh per-job tempdir.
3. Run `etal` as a subprocess with timeout (10–30s), scrubbed env,
   no network, CPU/mem limits. Capture stdout/stderr separately.
4. On success read the artifact bytes; on failure parse diagnostics.
5. Remove the tempdir in all cases (`EXIT/INT/TERM` traps).
6. Cache by content-hash of `(etal_version, target, files)` — ROM
   bytes are host-independent and deterministic, so cache hits are
   safe across users.

## Nix story

* Image build: `nix build .#default` on the pinned compiler flake
  (which ships `etal + vendor/` as one closure), then copy the
  closure into a slim runtime image. The API never shells to `nix`
  per-request (cold download latency); `nix run` is dev-only.
* No native Windows via Nix (upstream uses WSL); Windows/macOS rows
  arrive as committed `vendor/` binaries, cross-bundled from Linux.

## Features

v1 (this milestone):

* [x] `POST /compile` for `rom` + `web` targets
* [x] `GET /targets`, `GET /version`, `GET /health`
* [x] Diagnostic parsing to `{file, line, col, msg}`
* [x] Content-hash compile cache (identical requests return the original job)
* [x] Explicit-row bundles (`linux-*`, `macos-*`) once verified
* [x] Hardening: `X-API-Key` service auth + real user accounts (JWT
  access, rotating refresh, bcrypt), per-key/IP rate limits with
  `Retry-After`, compile slots with 503, CORS, security headers
* [x] Dockerfile with pinned compiler release (hash-verified GitHub
  asset, baked at build; `ARG ETAL_VERSION`)
* [x] Agent relay with automatic model selection: server-held keys,
  ranked routes, per-turn fallback, breaker and token budgets

v2 (deferred):

* [ ] `windows-x86_64` (blocked on vendored `uxn2.exe`)
* [ ] Headless run/preview endpoint (ROM execution is a separate
  trust domain — decide sandbox before promising it)
* [ ] Project persistence (or keep the backend stateless and let
  the frontend own storage)
* [ ] Compile queue + concurrency limits under load
