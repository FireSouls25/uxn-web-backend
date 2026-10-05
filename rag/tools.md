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
* `set_mask {def|object, x?, y?, w?, h?}` — hitbox inside the sprite
  (collision + clicks; drive keeps full bounds). Omit the numbers
  to clear. Out-of-sprite boxes are rejected.
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
  `sprite {sprite}` (swap art at runtime, same tile size as the
  leaf — the draw loop unrolls the declared size),
  `show`/`hide` (alive bit on/off without running destroy events),
  `play {sound}` (named one-shot SFX — see below),
  `song {song}` (start a named 4-voice loop in the shared
  sequencer; one loop plays at a time and keeps playing across
  scene switches), `song_stop` (silence it),
  `goto {scene}`,
  `overlay {scene}` (push the current scene, enter the target as a
  **fresh instance** — its setup runs again, so it is menu
  navigation, NOT freeze-pause), `back` (pop to the scene below;
  8 deep, overflow and popping an empty stack are silent no-ops),
  `destroy {target?}` (self default, else
  `any|solid|player|movable|def:<id>` unrolled statically like
  collide pairs, victim destroy fns run first under the alive
  guard, max 12 victims),
  `wait {ticks 1-255}` (arms the alarm event — a waiter without one
  is rejected, single timer per object),
  `wait {ticks, slot 0-3}` (one timer per slot; each waited slot
  needs an alarm event with the same slot on the same owner —
  alarm events carry the slot, slot 0 keeps the legacy fn name),
  `run {snippet}` (named ETAL snippet from the Code library — see
  below; the replacement for inline code),
  `code {code}` (legacy inline ETAL, same reserved-name gate as
  tick text — convert to a snippet + run block),
  `button {label 1-32, action 0-64}` (labeled annotation, lowers to
  a comment — never changes runtime bytes; menus are button labels
  + click/key events + goto scene chains, no interpreter in ROM).
* `add_snippet {name, code?}` / `set_snippet_code {snippet, code}` —
  the Code library: ETAL statements with the tick-text gate,
  authored once and connected to any event with run blocks.
  `delete` is refused while a run block names the snippet (same
  refcount rule as sounds).
* Variables, conditions, multi-alarm (visual rules with the same
  gate): `add_variable {name, init?}` (u16, boot init, one buffer
  slot each — hand snippets share `var_<id>[0]`),
  `rename_variable` (retargets set/if), `set_variable {variable,
  init}`, `delete_variable` (refused while referenced);
  `set {variable, set_mode set|add|sub, set_value}` (constant
  assign/+=/-=); `if {if_left, if_op, if_right}` (operands
  `kind:ref` — `var:id`, `const:N`, `pos:x|y`, `btn:up|down|left|
  right` over the live dpad; keyboard keys have no held state on
  either VM, so there is no held-key operand — key events stay
  press-edge); then/else hold nested block lists (max depth 3,
  addressed `1.then` in add/delete_block `path`).
* Dialogue labels: `set_label {object, text?}` — 1–24 printable
  ASCII chars drawn at the object's position in 8px 1bpp cells.
  Glyphs are prerendered into a per-leaf ROM blob at emit, so a
  labeled project never imports `lib/font.ux`. Per-instance state:
  a template never carries one, and the blits follow the alive bit
  (a destroyed object takes its label with it).
* `preview_event {def|object, event}` — the exact lines the emitter
  writes (same function; preview and build cannot disagree).
  `delete_event` / `delete_block {index}` for iteration.
* Frame order: song tick → input latch → drive → anims → scene
  transitions → object key → object click → step (+ legacy tick
  text, blocks first) → collide → alarm → custom/frameCode → draw. Collide is
  level-triggered while overlapping; destroyed leaves go quiet
  (draw, drive, collide and handlers all check the alive bit).
  Collide pairs unroll per scene (max 48 — narrow targets or split
  the scene); every event otherwise lowers to a small per-leaf fn,
  so generated functions stay far under the assembler budget.

## Rules as code

* `script_object {object, code}` — ETAL statements wrapped as
  `tick_<scene>_<id>(slot)`, called every frame. Full generated-state
  access (`ox[slot]`, scene fns). Only redeclarations are rejected.
* `creation_code {object, code}` — per-instance statements spliced
  after the leaf's create-event blocks (GameMaker Creation Code).
  Empty clears.
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
* Songs (music): `create_song {name}` — named 4-voice loop, **max 16
  steps per voice** (the live track buffers are 16 bytes per voice:
  128 bytes of RAM). `set_song_note {song, voice 0-3, step 0-15,
  pitch 0-107, len 1-255}` (omit pitch for a rest; pitch 127 is the
  rest marker from `lib/song.ux`),
  `set_song_vol {song, voice, vol 0-255}` (vol 0 = silent, and a
  voice with no audible notes never gets an Audio device),
  `rename_song {from, to}` (song blocks follow), `delete_song`
  (refused while a song block names it). One loop plays at a time
  through `song_tick()`, called once per frame from `on_frame` —
  a single shared square wave per voice, so tempo is the tick rate,
  not sample-accurate.
* `add_anim_frame {anim, sprite}` — append a frame (same tile size;
  max 16). `set_anim {anim, rate?, loop?, pingpong?}` — tune ticks
  per frame, wrap, bounce at the ends (needs loop + 2+ frames).
* `set_theme {r, g, b}` (0–65535) — the ONLY way to more colors:
  sprites stay 4 indices, but the 4 palette colors can be anything.
* `validate {}` — full gate (ids, refs, physics, cycles, slots).
* `emit_files {}` — deterministic ETAL `{devices.ux, main.ux}`.
