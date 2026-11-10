# One-VM deploy on AWS (arm64, e.g. t4g.micro)

The API and Caddy (HTTPS) on one small Graviton instance, plus a
Postgres container only when `DATABASE_URL` points at it (`@db:`). The
image is the main `backend/Dockerfile`: it downloads the pinned
compiler release for the host CPU (`etal-linux-aarch64.tar.gz` here,
v0.1.5 on) and verifies it against `compiler-sha256.txt`. To run a
compiler commit no release has, point `compose.yml` at
`Dockerfile.source` (context `../../..`) and deploy with
`UXN_DSL=../uxn-dsl`.

Deploy or update, from `backend/` (needs the instance's SSH key):

    sh deploy/aws/deploy.sh <elastic-ip> [~/.ssh/uxn-backend.pem]

It copies the committed `backend/` and `../uxn-dsl` trees plus your
`backend/.env` (as `backend.env`, unchanged), builds on the instance
and restarts. Commit first: uncommitted changes are not shipped.

Env on the server (`/opt/uxn/state/`):

* `backend.env`: your `.env`, replaced on every deploy. Change
  settings in your local `.env` and redeploy.
* `prod.env`: made once on the first deploy and kept: `DOMAIN`
  (`<ip>.sslip.io`, so Caddy can get a certificate without a domain),
  `PUBLIC_BASE_URL`, `POSTGRES_PASSWORD`, `DATABASE_URL`,
  `JWT_SECRET`. Server-only settings and secrets live here too
  (`CORS_ORIGINS`, `LLM_PREFERRED`, provider keys, a managed
  `DATABASE_URL`). It wins over `backend.env`. Values must not
  contain `$` (compose expands it).
* The compose file pins `ETAL_BIN`/`ETAL_SOURCE`/`COMPILER_DIR` to the
  image's compiler.

With a real domain: point an A record at the IP, set `DOMAIN` and
`PUBLIC_BASE_URL` in `prod.env`, then `docker compose up -d` in
`/opt/uxn/backend/deploy/aws`.

Logs: `ssh ubuntu@<ip> 'cd /opt/uxn/backend/deploy/aws && docker compose logs -f api'`.
