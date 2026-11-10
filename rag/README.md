# RAG corpus for agents

Single home for agent context. `frontend/scripts/sync-rag.sh` copies
`engine.md`, `tools.md`, `etal.md`, `varvara.md`, `examples.md` into the web bundle,
where they become the Pi system prompt (`src/lib/agent/pi.ts`).

* `engine.md` — the engine model (scenes, templates, events/actions,
  scripts↔engine contract, reserved names) + the agent's workflow.
* `tools.md` — how the tools in `frontend/src/lib/agent/tools.ts`
  combine (each tool's own description travels with the request).
* `etal.md` — ETAL language reference: types, narrowing, budgets,
  common compiler errors and their fixes.
* `varvara.md` — hardware facts verified against `uxn2/src/uxn2.c`.
* `examples.md` — chess in the engine model + small block patterns.
* `agent-contract.md` — the browser↔relay contract for `POST /agent/turn`
  and how the server picks the model.

Every user turn also carries a live project summary
(`projectContext()` in tools.ts, via `buildPrompt` in rag.ts), and
the agent can `read_file` any source and `check_build` with the real
compiler.
