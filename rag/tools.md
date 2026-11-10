# Agent tools: how to use them together

Source of truth: `frontend/src/lib/agent/tools.ts`. Each tool's own
description and params travel with the request; this page covers how
they combine. Tools edit the open project in the user's tab (the same
store the UI edits), so the user sees every change live.

## Looking before acting

* The request starts with a **project summary** (scenes, templates,
  events, scripts, sprites, sounds, variables). Trust it for ids.
* `describe` gives a compact snapshot again (after many edits).
* `scene_map {scene?}` gives positions and sizes of every object;
  `find_space` / `check_space` find free spots.
* `read_file {path, from?, to?}` returns numbered source: a script
  (`chess.ux`), `custom.ux`, a generated file (`main.ux`) or a
  library module (`lib/fix16.ux`). Read a script before editing it.
* `list_library {module?}` lists the standard modules or one module's
  exports.
* `preview_event` shows the ETAL an event lowers to.

## Building

* Art: `draw_sprite` (shapes, dithering, outline: the way to make
  detailed art, see craft.md), `create_tile_sheet` (16 8×8 cells for
  `set_tilemap`), `headline` (2× title type), `create_sprite` (raw
  pixels 0–3, for tiny things), `set_sheet`, `set_look`,
  `add_anim_frame` / `set_anim`, `set_theme` (the 4 colors).
* Things: `create_object_def` (template), `add_template_var`,
  `place_instance`, `put_on_scene` (one-off object), `extract_object`,
  `set_label` (text objects), `move_object`, `rename_object`,
  `delete_node`, `reorder_nodes`, `move_node`, `nest_scene`.
* Scenes: `create_scene`, `add_transition` (click/key → goto).
* Behaviour: `add_event {def|object|scene, trigger, …}` returns the
  event id; `add_action {…, event, action: JSON}` appends an action.
  Nested bodies go inline (`then`, `else`, `body`) or via `path`
  (`"2.then"`). Use `delete_event`, `delete_action` and
  `preview_event` to iterate.
* State: `add_variable` / `set_variable` / `rename_variable` /
  `delete_variable` (game vars), `add_template_var` (per instance).
* Sound: `create_sound` + `set_sound_voice`, `create_song` +
  `set_song_note` / `set_song_vol`; `play` / `song` actions use them.
* Code: `write_script {path, code}` (whole module, created or
  replaced), `delete_script`, `set_custom_code` (hooks),
  `set_loop` (engine | script).

## Checking (always, before saying "done")

1. `validate`: instant local checks (ids, references, reserved
   names, budgets the editor knows).
2. `check_build`: compiles with the real ETAL compiler on the server
   and returns `{file, line, msg}` diagnostics mapped to the user's
   files. Fix each one (`read_file` around the line, then
   `write_script`) and check again. Stop after 3 failed rounds and
   tell the user what is still wrong.
3. `emit_files` only when you need to see all of the generated code.

Export and playtest are UI buttons; tell the user to press Playtest.
