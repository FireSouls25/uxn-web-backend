# The engine model (how a uxn·forge game is built)

GameMaker/Godot-style: **scenes** hold **objects**, objects come from
**templates**, and behaviour is **events → actions** (the Blocks
view). Scripts (`.ux` modules) hold the logic that blocks are too
coarse for, and blocks call into them. The engine compiles everything
to one ETAL program (`main.ux` + your scripts).

## Pieces

* **Sprite**: art. 1×1 to 4×4 tiles of 8×8, 4 colors (indices 0–3).
  A sprite can be a **sheet** (`set_sheet`): an object shows one
  **cell**, and actions switch cells.
* **Template** (`create_object_def`): an object type, like "piece" or
  "enemy". It has default sprite and kind, **variables** (each instance
  keeps its own copy: `add_template_var`) and **events**.
* **Object / instance**: a template placed in a scene
  (`place_instance`), or an inline object (`put_on_scene`), which is
  its own one-off template. Instances keep their own position and
  variable overrides.
* **Scene**: a level or screen. It has objects, its own **scene
  events** (the level controller: start, keys, timers, clicks
  anywhere), and a **spawn pool** (free slots for `spawn` actions).
  `p.start` is the first scene. Text objects (`set_label`) draw
  strings; a `text` action redraws them with a number.
* **Game variables** (`add_variable`): global u16 values, `var_<id>[0]`
  in code. **Template variables**: per-instance u16, `iv_<name>[self]`
  in code.

## Levels bigger than the screen

* **Tile layer** (`set_tilemap`): a scene's grid of 8×8 cells from a
  tile sheet (a sprite whose cells are 1×1 tiles). One byte per cell,
  no object slots, so a 160×18 level is cheap. Listed cells are
  **solid** for movement. The layer sets the scene's world size.
  Users paint it in the Scene view (Tiles → paint).
* **Camera** (`set_camera`): follows the first live instance of a
  template, centered and clamped to the world. Objects marked **HUD**
  (`set_hud`) stay fixed on screen (scores, lives).
* **Movement presets** (`set_movement`) on a template:
  `platformer` (arrows run, up/A/space jumps, gravity; solid tiles and
  solid objects block) and `walker` (patrols, turns at walls and
  ledges: enemies). The `jump {power}` action launches either one
  upward (stomp bounces, springs). Mouse coordinates stay
  screen-space.

## Events (triggers)

create (scene starts / spawned), step (every frame), destroy,
key (named input or a controller button), click (pressed on this
object; on a scene: anywhere), release (mouse released anywhere),
collide (with any|solid|player|movable|def:<id>; the hit one is
`other`), alarm (timer 0–3, armed by `wait`), message (sent by `send`
to every object and the scene). Scenes have no collide/destroy.

## Actions (all values are u16 expressions)

move, set_pos, glide (tween over frames), sprite, cell, look (blend,
flips), show/hide, text, play (sound), song/song_stop, goto (switch
scene), overlay (open a scene on top and freeze the one below: pause
menus) / back, wait (timer → alarm), destroy (self|other|def),
spawn (new instance of a template), send (message), set (game var or
my/other var: set/add/sub), if/else, repeat, **call** (a script
function), code (raw ETAL, last resort).
Values: numbers, variables, `my`/`other` vars, x/y/cell/w/h of self or
other, mouse x/y, random, math, and **function calls** (script fns
that return u16). Conditions: compare, button held, touching, mouse
over, mouse down, all/any/not.

**Rule of thumb:** placement, art, sound, input, scene flow and
simple state go in blocks. Rules, AI, tables and anything with loops
over data go in a script function that blocks `call`.

## Scripts ↔ engine contract

* Scripts are imported after the engine's state, so they can read and
  write it. Public runtime names (slot arrays have 128 entries,
  indexed by slot; `self` is the running object's slot, `other` is the
  collide partner):
  `ox[s] oy[s]` (u16 position), `ocell[s]` (u16 cell), `odef[s]` (u8
  template index), `oflags[s]` (u8: bit0 alive, bit1 solid, bit2
  movable, bit3 visible, bit4 player), `ocount`, `scene` (u8 current
  scene id, compare with `SC_<SCENE>` constants), `scene_go`,
  `var_<id>[0]`, `iv_<name>[slot]`.
* Functions called from blocks take **u16 params only** and return
  **u16** when used as a value (`-> u16`). Keep them small: every
  param is a permanent zero-page byte.
* Optional hooks in `custom.ux` (`set_custom_code`):
  `custom_setup()` (boot), `custom_frame()` (each frame),
  `custom_draw()` (after drawing: HUDs), `custom_key()` /
  `custom_mouse()` (inside the vectors; latches only).
* **Reserved names** (declaring them is an error): anything starting
  `e_`, `spr_`, `var_`, `iv_`, `SC_`, plus `ox oy ot om ow oh otw oth
  osw oflags odef osp ocell omx omy omw omh otx onv onf ogx ogy ogn
  oan oaf oak oad oa0-3 st0-3 ocount scene self other scene_go start
  main on_frame on_key on_mouse sq32 sg_on custom_*` (the hooks are
  yours to define, but only once).
* Library modules: `list_library` lists them; `import "fix16.ux"` in a
  script ships it. Modules that declare engine names (scene.ux) can't
  be used in the engine loop.
* The **script loop** (`set_loop script`) is the alternative for
  hand-written games: `custom_frame()` is the whole game, there is no
  scene runtime, and generated `assets.ux` exposes `&spr_<id>`,
  `snd_<id>()`, `draw_scene_<id>()` and `scn_<id>` layout tables.
  Prefer the engine loop unless the user asks otherwise.

## Limits that shape designs

128 object slots in total; the per-scene spawn pool is reserved
from those. Collide pairs unroll per scene (keep targets narrow).
4 audio voices; songs hold up to 16 steps per voice. Zero page is
256 bytes and the engine uses much of it, so big games keep state in
`buffer` arrays. Colors: 4 palette entries (`set_theme`); blends
0/5/10/15 make color 0 transparent (pieces on a board use blend 0
or 5).

## Workflow the agent must follow

1. The project summary at the top of each request is current. Use it,
   and call `describe` / `scene_map` / `read_file` for details instead
   of guessing ids.
2. Plan in terms of templates, events and scripts before editing.
3. Make the edits with tools. Read scripts before rewriting them
   (`read_file`) and keep what you don't need to change.
4. `validate` (instant, local), then `check_build` (real compiler),
   and fix every error it names, repeating until clean.
5. Reply with a short summary of what changed and how to try it
   (Playtest).
