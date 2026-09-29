# Agent tools reference

Source of truth: `frontend/src/lib/agent/tools.ts` (`TOOLS` + `runTool`).
Every tool validates like the UI; the backend compiler is final arbiter.

## Project & scenes

* `describe()` — snapshot `{project, scene, scenes, sprites, anims}`. Start here.
* `create_project {name}` / `open_project {id}` — fresh project / switch.
* `create_scene {}` — empty scene, switches to it.
* `nest_scene {scene, into?, x?, y?, name?}` — instance a subscene at an
  offset (Godot-style). Cycles rejected. Children render at parent
  offset + own position; editing them edits the home scene.

## Sprites & objects

* `create_sprite {name, pixels?}` — 8×8 library sprite. Pixels are 64
  indices 0–3 (**Uxn 2bpp hardware limit**); index 0 paints palette 0
  (opaque background, not transparency).
* `put_on_scene {sprite, scene?, x?, y?, kind?, name?}` — kinds:
  `player` (≤1/scene, 0 allowed), `static`, `movable` (solid+pushable).
* `move_object {object, x, y}` — absolute pixels, clamped.
* `rename_object {from, to}` — click bindings follow.
* `delete_node {index, scene?}` / `reorder_nodes {from, to}` /
  `move_node {from_scene, from_index, to_scene}` — draw order follows
  list order; branch moves are cycle-guarded.

## Rules as code

* `script_object {object, code}` — ETAL statements wrapped as
  `tick_<scene>_<id>(slot)`, called every frame. Full generated-state
  access (`ox[slot]`, scene fns). Only redeclarations are rejected.
* `script_scene {scene?, code}` — statements at the end of the frame.
* Project `customCode` adds top-level declarations plus optional
  `custom_setup()` (boot) / `custom_frame()` (every frame) hooks.

## Sound, theme, build

* `set_sound {voice 0-3, note 0-107, vol 0-255}` — mix plays once on
  boot via Audio0–3, shared square wave.
* `set_theme {r, g, b}` (0–65535) — the ONLY way to more colors:
  sprites stay 4 indices, but the 4 palette colors can be anything.
* `validate {}` — full gate (ids, refs, physics, cycles, slots).
* `emit_files {}` — deterministic ETAL `{devices.ux, main.ux}`.
