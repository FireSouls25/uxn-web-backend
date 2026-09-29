# Agent chat contract (DRAFT — Pi SDK wiring pending)

## What exists today

* Tool manifest with local execution: `frontend/src/lib/agent/tools.ts`
  (`runTool(name, args)` — tested, no network).
* This corpus (`backend/rag/`): `tools.md`, `etal.md`, `varvara.md`.
* Chat shell UI: `frontend/src/components/AgentChat.tsx` (spawn/hide,
  provider selector, offline state).

## Proposed endpoint (not implemented)

`POST /agent/chat` `{provider, model, messages, project_id}` →
server-side tool loop: model emits `tool_call {name, args}`, backend
runs the equivalent operation against the stored project (same
validation as `POST /compile`), appends tool results, repeats until a
final message or step budget. Responses stream (SSE).

## Needed from the Pi integration

1. SDK package name + docs URL (exact import, client init, auth).
2. Provider list Pi supports (model ids, tool-calling support per model).
3. Web-components package name (to prefer it over the custom shell).
4. Whether tool calls run client-side (current manifest) or must be
   re-implemented server-side (affects `POST /agent/chat` design).
