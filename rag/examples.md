# Example: chess in the engine model (the pattern to imitate)

The Chess example (read-only; "Duplicate to edit") is built the way
the engine intends. Use it as the reference for "blocks for the
game's shape, scripts for its rules".

* **Scenes**: `menu` (title + PLAY button), `side` (WHITE/BLACK
  buttons set game var `side`), `play` (board tiles, 32 pieces, text
  objects title/turn/score, FLIP and MENU buttons, `pool: 28` spawn
  slots for move dots), `pause` and `over`. `pause` and `over` open
  with `overlay` so the board stays frozen underneath; RESUME/Esc use
  `back`. Inputs: `confirm` (Enter) and `pause` (Esc).
* **Art**: `board4` (32×32 board tile used by `tile`), a `pieces`
  sheet (one cell per piece kind and color), `dot`, `cursor`.
* **Templates**:
  * `tile`: board squares.
  * `piece` (blend 0, template var `sq`): on create it sets
    `sq = layout_sq(x, y)`, calls `put_piece(my sq, cell + 1)` and
    snaps to `sq_x/sq_y`. Each step, if `grabbed_sq() == my sq`, it
    follows the mouse. On message `moved`: if it was captured it
    destroys itself; if it moved, it sets `sq = move_to()`, glides to
    the new square and updates its cell. On message `rook` it does the
    castling hop.
  * `dot` (spawned move markers), `icon`, and `pointer`, which follows
    the mouse on step (the game draws its own cursor).
* **Scene events (play)** form a small message pipeline:
  * create: `new_game(var side)`, then wait 20 (timer 0, so the AI
    moves first when the human plays black).
  * click: `r = press(mouse x, mouse y)`. Then r=1 plays select and
    sends `select`; r=2 sends `turn_done`; r=3 sends `deselect`; r=4
    plays bad.
  * release: `r = release(...)`. Then r=2 sends `turn_done`; r=5 sends
    `relayout` (a drag that snapped back).
  * message `select`: destroy every `def:dot`, then `repeat 64` (index
    `i`) spawns a dot where `is_target(i) == 1`.
  * message `turn_done`: sends `deselect` and `moved` (and `rook` when
    castling), plays capture/move/check, then waits on timer 1 if
    `status() >= 2` (game over) or on timer 0 for the AI.
  * alarm 0: on the AI's turn, `ai_move()` and then `turn_done`.
    Alarm 1: overlay `over`.
  * The turn and score text objects redraw with `text` + function
    values (`material_white()`).
* **Scripts**:
  * `rules.ux`: pure chess over `buffer board[64]`.
  * `pieces.ux`: constants.
  * `chess.ux`: the glue that blocks call. Every fn takes u16 params,
    returns u16 values, and narrows with `to8` (a `u16 mod 256`
    bridge). It keeps state in a few globals and buffers to stay
    inside the zero page.
* **Sounds and song**: select, move, capture, bad, check, menu, go,
  and a fanfare song on mate.

Budget lessons from building it: zero page ended at 242/256 and the
label dictionary was near full. The fixes were RAM buffers instead of
globals, fewer locals, shared helpers, and one sheet instead of 12
sprites.

## Example: Sprout Run (platformer, no scripts)

* 256×192. Scenes `title`, `level`, `win`, `lose`, each with a tile
  layer. The level layer is 160×24 from sheet `tiles` (16 cells:
  grass, grass edges at pits, dirt, deep dirt, brick, stone, ledge are
  solid; bush, flower, fence, cloud pieces and dithered hills are
  decor). The other scenes share a one-screen backdrop. The level has
  `camera: follow hero`.
* Art is drawn from shapes (see craft.md): a shaded, outlined hero
  with eyes and leaves, a beetle with a shell shine, a waving flag, and
  a 4-frame `spin` animation on the coin template.
* Templates: `hero` (platformer, speed 2, jump 5, mask 2,4,12×12,
  blend 5), `beetle` (walker, speed 1), `coin` (anim spin), `flag`.
* Hero events:
  * collide coin → destroy other, coins +1, play coin, send `score`
    (the HUD text redraws `X ` + var coins).
  * collide beetle → if `my y + 12 < other y` (falling onto it):
    destroy other, jump 3, play stomp; else play hurt and goto lose.
  * collide flag → goto win.
  * step: if y > 192 (fell in a pit) → lose.
* HUD: a coin sprite with the spin anim plus that text, both `hud`.
  Title and end screens: `headline` type, a walking beetle, and
  blinking prompts.

## Example: Star Catcher (arcade, no scripts)

* 256×192 night city: one tile layer shared by all scenes (starfield,
  horizon glow, buildings with roofs, lit or dark windows and antennas,
  dithered ground). A cratered moon sprite with a dithered terminator.
* Templates:
  * `basket` (player; mask covers only the rim; held left/right
    moves it).
  * `star` (anim twinkle; picks a random speed on create).
  * `bomb` (anim fizz).
  * `life`: a heart with var `n` that hides on `hud` when
    `lives < my n`; three instances with n = 1, 2, 3.
* Play scene: create resets score/lives, sends `hud`, waits 30;
  alarm spawns a bomb 1 time in 5, else a star, at x = random 224 + 8,
  then waits 30.

## Example: Life Lab (a website, blocks + one script)

* 320×240, theme paper/ink/teal/coral. Scenes `home`, `lab`, `about`
  are pages; every page repeats the nav nodes (logo, site name, link
  text objects whose click event is `goto <page>`; the current link
  gets an underline instead of an event).
* Layout is a **tile layer** per page (sheet `layout`, 16 1×1 cells:
  nav fill, rounded card edges, dish dots, 6 pill-button pieces) with
  no solid cells, so it costs no object slots.
* Buttons are text objects over a pill (label padded with spaces, a
  `mask` that covers the pill) with a click event.
* `life.ux` keeps the 48×32 grid in `buffer cells[1536]: u8` and
  exports `life_step`, `life_random`, `life_clear`, `life_paint(mx,
  my)`, `life_gen() -> u16`, `life_pop() -> u16`. It draws the cells in
  `custom_draw` (guarded by `scene != SC_LAB`) after the engine pass.
* Lab scene events: create → `life_random`, running = 1, wait 4;
  alarm → if running, `life_step`; send `stats`; wait 4; step → if
  mouse down, `life_paint(mouse x, mouse y)`. GEN/POP text objects
  redraw on `stats` with a function value.

## Small patterns

* **Score HUD**: game var `score`; a text object `hud` "SCORE"; on a
  coin's collide with player: set score add 1, then `text` on hud
  with value var score, then destroy self.
* **Timer spawner**: scene create: wait 60 slot 0. Scene alarm 0:
  spawn enemy at (random 160, 0), then wait 60 slot 0 again.
* **Pause**: scene key escape → overlay pause. Pause scene key escape →
  back.
* **Script value**: `dist :: fn(a: u16, b: u16) -> u16 { if a > b {
  return a - b; } return b - a; }` used in an if condition: compare
  `call dist(my x, other x)` lt 16.
