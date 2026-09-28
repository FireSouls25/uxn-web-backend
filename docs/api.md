# API reference

Interactive version: `GET /docs` (Swagger UI) on a running server.
All messages are bilingual — see *Language* below.

## Endpoints

### `GET /health`
Liveness. `{"status": "ok"}`. Public, no key.

### `GET /version`
`{"backend": "0.1.0", "etal": "0.1.3"}`. Public. The frontend sends
its expected `etal` in every compile; mismatches are rejected (409).

### `GET /targets`
Supported vs coming-soon rows, with `vendored` flags read live from
`etal --list-targets`. Public. The frontend dropdown should render
from this, never from a hardcoded list.

### `POST /compile`
Compile a file tree. Requires `X-API-Key` when `API_KEYS` is set.

```json
{
  "target": "linux",
  "mode": "bundle",
  "entry": "main.ux",
  "files": {"main.ux": "main :: fn() {\n    print(\"hi\");\n}\n"},
  "etal_version": "0.1.3",
  "lang": "en"
}
```

* `target`: `linux` or anything else. Other known rows
  (`macos-*`, `windows-*`, …) return 400 *coming soon*; unknown ids
  return 400 *unknown target*.
* `mode`: `bundle` (default), `tal` (`-t`, text under `artifacts.tal`),
  `rom` (`-r`, `artifacts.rom_b64`). Bundles return `bundle_b64`
  (`linux`) or `html_b64` (`web`).
* `files`: relative paths only (`..` and absolute paths rejected),
  extensions `.ux` `.chr` `.icn` `.wav`, 64 files / 256KB each / 2MB
  total by default.
* Response: `{status: ok|error, job_id, message, diagnostics:
  [{file, line, col, msg}], cached: bool, artifacts}`. A repeated
  identical request returns the original `job_id` with `cached: true`
  and no recompile.

### `GET /jobs/{job_id}`
Re-fetch a stored job with its artifacts. Same auth as `/compile`.
404 when unknown.

## Auth (`/auth/*`)

Real user accounts in Postgres. Passwords are bcrypt hashes; access
tokens are short-lived JWTs, refresh tokens rotate (each use revokes
the old one) and can be revoked via logout.

| Call | Body | Result |
|---|---|---|
| `POST /auth/register` 201 | `{name, email, password, lang?}` | `{access_token, refresh_token, expires_in}` |
| `POST /auth/login` | `{email, password, lang?}` | token pair (401 hides unknown vs wrong) |
| `POST /auth/refresh` | `{refresh_token}` | new pair, old refresh revoked |
| `POST /auth/logout` | `{refresh_token}` | revokes (idempotent) |
| `GET /auth/me` | `Authorization: Bearer …` | `{id, name, email}` |

`/compile` and `/jobs/*` accept **either** a user bearer token **or**
the service `X-API-Key` (open dev mode when neither is configured —
set `API_KEYS` plus `JWT_SECRET` in production). Auth endpoints have
their own strict budget (`AUTH_RATE_PER_MINUTE`, default 10/min per
IP) as a brute-force brake.

## Status codes

| Code | Meaning |
|---|---|
| 200 | Compiled (check `status` field), or cached hit |
| 400 | Bad target / bad tree (`detail` is localized) |
| 401 | Missing or wrong `X-API-Key` (only when `API_KEYS` is set) |
| 404 | Unknown job |
| 409 | `etal_version` mismatch |
| 429 | Rate limit; `Retry-After` header carries the wait in seconds |
| 503 | All compile slots busy |
| 504 | Compiler timed out |

## Language

Every user-facing string exists in English and Spanish
(`app/i18n.py` — the only place new strings may be added). The
`lang` field (`en`|`es`, default `en`) wins; otherwise the
`Accept-Language` header is honored. Error diagnostics from the
compiler itself (`file:line:col: msg`) stay in the compiler's
language; only the surrounding API messages translate.
