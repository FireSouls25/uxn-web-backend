# Deploying: backend on Render, frontend on Vercel

Two git repositories, two platforms, one contract: the browser knows
exactly one backend URL (`PUBLIC_API_URL`) and the backend must accept
that site's origin. Everything else is configuration.

Order matters, and the order is *not* obvious, because each side needs
an address the other one has not published yet:

1. backend (needs the frontend's origin for CORS — you can fill this
   in with `*`-free placeholders and fix it in step 3),
2. frontend (needs the backend's URL, which step 1 gives you),
3. backend again (now with the real frontend origin), or just restart
   it after editing the env var.

Nothing here is automatic. The manual steps are listed as such.

---

## 1. Backend on Render

**1.1 Push the backend repo to GitHub** and connect it:
*New → Web Service → pick the repo*. Render finds the `Dockerfile`
automatically; leave *Environment* = **Docker**, region = whatever is
closest to your users.

The image bakes the compiler: at build time it downloads the pinned
`etal` release from GitHub and refuses it unless the sha256 is listed
in `compiler-sha256.txt`. **The first build is slow** (Python deps +
~compiler download). It needs outbound network to `ghcr.io` and
`github.com`; nothing else is fetched at runtime.

**1.2 Create the database.** *New → Postgres*, same region. Choose
the plan deliberately: Render's free Postgres is deleted after 30
days. When you link the database to the web service (*Postgres →
Connect*), Render injects `DATABASE_URL` for you — check the service's
Environment tab and leave it if it is there.

**1.3 Environment variables** (service → Environment):

| Variable | Value | Notes |
|---|---|---|
| `DATABASE_URL` | injected by Render | Use the **internal** URL (`dpg-…` host), not the external one. Already set if the database is linked. |
| `API_KEYS` | a long random string | Service key for `/agent/*` operator endpoints (`X-API-Key`). **Without it every endpoint is open to the internet.** |
| `JWT_SECRET` | `openssl rand -hex 32` | With the default, anybody can forge a login token. |
| `CORS_ORIGINS` | the Vercel origin(s) | Filled in at step 3. Comma-separated, no spaces, no trailing slash. |
| `OPENROUTER_API_KEY` | your key | Optional: without any provider key the agent returns 503 and nothing else works. The free `:free` models cost nothing. |
| `AGENT_ALLOW_GUESTS` | `0` | Optional but recommended once the site is public: agents for signed-in users only. |
| `LOG_LEVEL` | `INFO` | Optional. `WARNING` silences the per-turn routing lines. |
| `PORT` | **leave it alone** | Render sets it; the image binds it. |

Nothing else is needed: `ETAL_*` is baked at build time, and the
proxy headers the rate limits depend on are configured in the image's
command.

Generate the two secrets:

```sh
openssl rand -hex 32   # JWT_SECRET
openssl rand -hex 24   # API_KEYS
```

**1.4 Service settings:** health check path `/health`. Instance type:
the free plan (512 MB, spins down after 15 idle minutes) is enough to
try, but a compile plus a model turn is a lot for 512 MB — use a paid
instance before promising anyone a demo.

**1.5 Verify** (wait a minute after the first deploy):

```sh
curl -s https://<service>.onrender.com/health
# {"status":"ok","compiler":"ok","database":"ok"}

curl -s https://<service>.onrender.com/version
# {"backend":"0.1.0","etal":"0.1.3"}
```

`database: "unavailable"` means the service is up but the schema is
not in place yet; it retries in the background and flips on its own —
no restart needed. If it stays that way, `DATABASE_URL` is wrong (a
common cause: the *external* URL pasted into the internal field).

Then check the routing, which is the one thing a deploy can get
subtly wrong (a key that did not make it into the environment, a
provider disabled, a budget spent):

```sh
curl -s -X POST https://<service>.onrender.com/agent/route \
  -H "X-API-Key: $API_KEYS" -H 'Content-Type: application/json' -d '{}'
```

`chosen` should be a free model of the provider whose key you set.
`excluded` explains every other row — that is where a missing key
shows up as `no key (OPENROUTER_API_KEY)`.

---

## 2. Frontend on Vercel

**2.1** Push the frontend repo, *Add New → Project* → import it. Vercel
detects Astro; `vercel.json` pins the build (`npm run build`) and the
output (`dist`), so there is nothing to fill in.

**2.2 Environment variable** — this is the only setting:

| Variable | Value |
|---|---|
| `PUBLIC_API_URL` | `https://<service>.onrender.com` (no trailing slash) |

Set it for **Production and Preview** both. It is inlined into the
JavaScript at build time, so changing it later requires a redeploy
(Deployments → ⋯ → Redeploy), not a settings edit. A `localhost`
value produces a site that only works on your machine.

**2.3 Deploy.** The first build is a `npm install` plus an Astro
build; nothing else is needed, and there is no server to run.

---

## 3. Finish the CORS loop

The backend was deployed before the frontend existed, so its
`CORS_ORIGINS` does not know the site yet. Set it and redeploy the
backend (Render: Environment → edit → *Save & Deploy*):

```
CORS_ORIGINS=https://<your-project>.vercel.app,https://<custom-domain>
```

Include the preview domain too if you use previews — otherwise agent
and compile calls fail the preflight from a preview URL. No trailing
slashes, no spaces, and no quotes around the whole value.

Then, with the browser console open, load the site and:

* `/studio` shows the target list (proves `GET /targets` + CORS),
* the agent answers a message (proves `POST /agent/turn` + the
  provider key).

A `CORS` error in the console with a 200 response means the origin
is missing from `CORS_ORIGINS` — the backend is fine.

---

## What this deployment is, and is not

Honest limits, all of them by design rather than by accident:

* **One worker, on purpose.** Rate limits, the agent turn budget and
  the compile slots are in-process. Never set `WEB_CONCURRENCY` on
  Render: every extra worker multiplies every quota. A shared store
  (Redis) is the fix when you need to scale out.
* **No migrations.** Tables are created with `create_all` at boot. A
  schema change means a new database (`dpg` → delete, recreate) or a
  manual `ALTER TABLE`; Alembic lands before any production data
  matters.
* **The compiler is baked into the image.** Bumping `ETAL_VERSION`
  means a new sha256 row in `compiler-sha256.txt` *and* a rebuild.
* **Artifacts live in Postgres.** Compiled bundles are stored as
  blobs in the jobs table; fine at this scale, not at a large one.
* **Free tiers are free but rate-limited.** The OpenRouter `:free`
  models throttle per account; the relay falls through to the next
  candidate, and the daily token budget per provider is the hard
  ceiling. If the fallback chain is empty, the agent says "no model
  available" and the logs say why.
* **The service key is an operator secret.** It goes in Render's
  environment, never in the frontend bundle. The browser cannot reach
  the operator endpoints at all.

See `operations.md` for the runtime knobs (keys, budgets, kill
switches, logs) and `api.md` for the endpoints.
