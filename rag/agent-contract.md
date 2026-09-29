# Agent chat contract

## How it works now

The loop runs in the browser, the model does not.

1. `frontend/src/lib/agent/pi.ts` holds the `pi-agent-core` `Agent`
   with our tool manifest adapted to Pi tools. A custom `streamFn`
   replaces the SDK's provider calls: every turn POSTs the transcript
   and the tool schema to `POST /agent/turn`.
2. The backend (`app/llm_relay.py`) picks the model, holds the keys,
   and walks its own candidates until one answers. The client cannot
   name a provider, model or key — the request model refuses extra
   fields.
3. The reply (text + `tool_calls`) is replayed as Pi's event stream
   (`text_*`, `toolcall_*`, `done`), so Pi drives the loop exactly as
   it would against a native provider.
4. `runTool(name, args)` executes in the tab against the open project.
   Tools mutate local state; the project is compiled through
   `POST /compile` as usual.

## How the model is picked

`llm.plan(db)` is the single decision point — the browser cannot
influence it, and it runs per turn. Hard filters first, each one
recorded in `excluded` with its reason: capability (tool calling) →
operator kill switch → key present → breaker cooldown → daily token
budget. Survivors are sorted free-first, then cheapest tier, then
quality, then admin weight (ties keep registry order), and the head
is the route for this turn.

If it does not answer, the relay moves to the next candidate in that
order: transport errors, 429 and 5xx trip the breaker, a bad request
moves on too (the next provider speaks the same protocol). Rows
without an OpenAI-compatible endpoint are skipped without being
called, and never trip a breaker. Everything here is exact data —
registry, env, ledger, clock — so the decision is deterministic,
logged (`llm route capability=… chosen=…`, `relay turn answered by
…`) and previewable with `POST /agent/route` (service key), which
calls the very same function. Provider identity stays in the logs and
the service endpoints; `/agent/turn` answers with a status only.

Providers with `dynamic: true` (a local model server) have no fixed
model list: `OLLAMA_MODEL` names the id automatic routing may use, and
`LLM_DEV_BASE_URL` registers any OpenAI-compatible endpoint as a
top-ranked free candidate. Both are read from the environment on every
call, like the keys.

## Endpoint

`POST /agent/turn`

```jsonc
// request — the entire contract
{ "messages": [ /* system|user|assistant|tool */ ], "tools": [ /* OpenAI function schema */ ] }
// response
{ "content": "…", "tool_calls": [ { "id", "name", "arguments" } ], "usage": { "in", "out" } }
```

Non-streaming today: one relay call per turn. Streaming would move to
SSE on the same route without changing the payload. Errors are a
status (429 rate limited, 401 sign-in required, 502 every candidate
failed, 503 nothing was callable); provider identity never appears in
the response.

`POST /agent/llm` is the pinned variant for operator tooling and the
future Node agent service (service key required). It re-checks
eligibility, so a pin cannot outlive a kill switch, a cooldown or a
budget.

## Still open

* A server-side tool loop (`POST /agent/chat`) so the agent can act on
  a *stored* project instead of the open tab — needed for background
  edits and for the chess showcase's read-only projects.
* Streaming (`text/event-stream`) on `/agent/turn` for token-by-token
  replies; the UI already renders `text_delta` and would not change.
* Image inputs: `toWireMessages` flattens them to `[image]`; the
  vision path needs a real multipart or content-block mapping.
* Native upstream APIs (anthropic, google) are ranked but skipped —
  a native call path would widen the candidate pool without touching
  the decision rules.
