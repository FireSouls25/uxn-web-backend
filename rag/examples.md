# Example: chess (the pattern to imitate)

Full game in `frontend/src/lib/examples/chess/` (sources synced from
`uxn-webpage/chess/`). Two layers, and custom code must follow them:

1. **Pure rules** (`rules.ux`): everything over `buffer board[64]`
   (squares 0–63, pieces 0 empty / 1–6 white / 7–12 black) — move
   generation per piece, `attacked`, `in_check`, `gen_legal`,
   `game_status`, `do_move`/`undo_move`, `setup_board`, plus a
   material AI (`ai_choose`). No devices, no vectors, no imports
   except data. This file splices verbatim into `customCode`.
2. **Thin device UI** (`game.ux`): five scenes (menu/color/play/pause/
   over), mouse drag-to-move, highlights, a cursor tile (the VM draws
   no cursor), SFX. Scenes map 1:1 onto the visual scene model;
   `tile_addr(kind, side)` computes ROM addresses arithmetically.

## Example: emitted demo (what visual projects lower to)

`devices.ux` (System/Screen/Controller/Mouse/Audio pages) + `main.ux`:
`meta`, `SC_*` constants, `data spr_*` 16-byte planar blobs, fixed
slot buffers, `scene_go`, `pt_in_rect`/`overlap88`, `draw_all`,
`setup_<scene>` + `<scene>_frame`, vector wiring, `main :: event`.
Frame order: input latch → drive → anim → clicks/keys → ticks →
custom_frame → scene script → draw.

## Conventions for generated + custom coexistence

* Generated identifiers (`ox`, `scene_go`, `SC_*`, `spr_*`, `atick_*`,
  `setup_*`, `*_frame`) are reserved; the checker rejects
  redeclarations, the compiler catches the rest.
* Hooks: `custom_setup()` runs once at boot, `custom_frame()` runs
  every frame before drawing. Per-object `tick_<scene>_<id>(slot)`
  gets the slot index for `ox[slot]`-style access.
* One player per scene max (0 allowed); movable implies solid;
  clicks accept nested paths (`rack/jar`); latch input in vectors,
  consume in frames.
