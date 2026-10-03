# Agent tools reference

Source of truth: `frontend/src/lib/agent/tools.ts` (`TOOLS` + `runTool`).
Every tool validates like the UI; the backend compiler is final arbiter.

## Project & scenes

* `describe()` — snapshot `{project, scene, scenes, sprites, defs, sounds, anims}`. Start here.
* `create_project {name}` / `open_project {id}` — fresh project / switch.
* `create_scene {}` — empty scene, switches to it.
* `nest_scene {scene, into?, x?, y?, name?}` — instance a subscene at an
  offset (Godot-style). Cycles rejected. Children render at parent
  offset + own position; editing them edits the home scene.

## Sprites & objects

* `create_sprite {name, pixels?, w?, h?}` — library sprite, 1×1 to
  4×4 tiles of 8×8 (bigger art = consecutive tiles, drawn tile by
  tile). Pixels are 64·w·h indices 0–3 (**Uxn 2bpp hardware limit**),
  tile-major row-major; index 0 paints palette 0 (opaque background,
  not transparency).
* `put_on_scene {sprite, scene?, x?, y?, kind?, name?}` — kinds:
  `player` (≤1/scene, 0 allowed), `static`, `movable` (solid+pushable).
* `move_object {object, x, y}` — absolute pixels, clamped.
* `rename_object {from, to}` — click bindings follow.
* `delete_node {index, scene?}` / `reorder_nodes {from, to}` /
  `move_node {from_scene, from_index, to_scene}` — draw order follows
  list order; branch moves are cycle-guarded.

## Object templates (Object ≠ Sprite ≠ Instance)

* `create_object_def {name, sprite?, kind?, anim?, tick?}` — behavior
  template: default art, kind (`player` ≤1/scene, `static`,
  `movable` = solid+pushable), animation, tick script. Editing the
  def updates every instance at once.
* `place_instance {def, scene?, x?, y?, name?}` — stamp an instance;
  position is always instance state, other fields override per
  instance (explicit values win, even `false`).
* `extract_object {object}` — top-level inline leaf becomes a
  template + instance pair (id and position stay, the rest moves
  into the new def). Prefer this over rebuilding shared art by hand.
* Leaves resolve at flatten time (local over def), so the emitter,
  canvas and validation all see effective values. Physics rules,
  player caps and anim-size matches check resolved leaves.

## Events & blocks (visual rules that compile to ETAL)

* `add_event {def|object, trigger, key?, target?}` — loop moments:
  `create` (scene enter), `step` (every frame), `destroy`,
  `key` (named input), `collide` (overlap vs
  `any|solid|player|movable|def:<id>`), `click` (press on it),
  `alarm` (slot countdown hits 0). One event per trigger+key/target.
  Defs own their events; instances run them (no local event lists).
* `add_block {def|object, event, op, ...}` — one action = fixed ETAL:
  `move {dx,dy}` (pixels, clamped), `set_pos {x,y}`,
  `play {sound}` (named one-shot SFX — see below),
  `goto {scene}`, `destroy` (self; runs the destroy event first),
  `wait {ticks 1-255}` (arms the alarm event — a waiter without one
  is rejected, single timer per object).
* `preview_event {def|object, event}` — the exact lines the emitter
  writes (same function; preview and build cannot disagree).
  `delete_event` / `delete_block {index}` for iteration.
* Frame order: input latch → drive → anims → scene transitions →
  object key → object click → step (+ legacy tick text, blocks
  first) → collide → alarm → custom/frameCode → draw. Collide is
  level-triggered while overlapping; destroyed leaves go quiet
  (draw, drive, collide and handlers all check the alive bit).
  Collide pairs unroll per scene (max 48 — narrow targets or split
  the scene); every event otherwise lowers to a small per-leaf fn,
  so generated functions stay far under the assembler budget.

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
* `create_sound {name}` — named one-shot SFX (up to 4 voices each;
  index = Audio device, vol 0 = silent). Trigger it after boot with
  `play {sound}`; an all-silent sound is rejected.
* `set_sound_voice {sound, voice 0-3, note 0-107, vol 0-255}` —
  voice editor for the library (same ranges as the boot mix).
* `rename_sound {from, to}` — play blocks follow. `delete_sound`
  is refused while a play block names the sound.
* `set_theme {r, g, b}` (0–65535) — the ONLY way to more colors:
  sprites stay 4 indices, but the 4 palette colors can be anything.
* `validate {}` — full gate (ids, refs, physics, cycles, slots).
* `emit_files {}` — deterministic ETAL `{devices.ux, main.ux}`.
