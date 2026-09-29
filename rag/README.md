# RAG corpus for agents

Single home for agent context. `frontend/scripts/sync-rag.sh` copies
`tools.md`, `etal.md`, `varvara.md`, `examples.md` into the web bundle,
where they become the Pi system prompt (`src/lib/agent/pi.ts`).

* `tools.md` — mirrors `frontend/src/lib/agent/tools.ts`; update both.
* `etal.md` — emitter-subset language cheat sheet.
* `varvara.md` — hardware facts verified against `uxn2/src/uxn2.c`.
* `examples.md` — chess two-layer pattern + emitter conventions.
* `agent-contract.md` — the browser↔relay contract for `POST /agent/turn`
  and how the server picks the model.
