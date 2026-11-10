# Craft: make it look finished (the default style)

Unless the user asks for something rough or minimal, build things the
way the examples are built (Life Lab, Sprout Run, Star Catcher): a
real title screen, art with shading and outlines, scenery from tiles,
big headline type, and a HUD that uses icons. A plain sprite on a blank
screen is a placeholder, not a result.

## 1. Canvas and palette first

* **Size**: 256×192 for games, 320×240 for page-like apps (more room
  for detail and text). 160×144 only when the user wants a tiny,
  handheld look. Set it before placing anything.
* **Four colors with roles** (`set_theme`; r/g/b are 4 nibbles, one per
  color c0..c3):
  * c0 is the background, and transparent under blends 0/5/10/15.
  * c1 is the outline/ink color (darkest on light themes, lightest on
    dark ones). Text objects draw in c1.
  * c2 and c3 are the two accents: one cool, one warm.

  Proven sets:
  * Day sky: `r 0x926e, g 0xc2a9, b 0xd343` (sky, ink, green, orange).
  * Night: `r 0x1ffe, g 0x2fd6, b 0x5fa9` (navy, moonlight, yellow,
    pink).
  * Paper: `r 0xe22f, g 0xe2b7, b 0xf495` (paper, ink, teal, coral).
* Text objects draw in c1, so pick a background that c1 reads on.

## 2. Art: use `draw_sprite`, not raw pixels

Recipe for a character or item (16×16 or 32×32):
1. Fill the big shapes: `ellipse` or `disc` for bodies, `rect` for
   boxes, `line` for stems and poles.
2. Shade one side: `dither` the main color to a darker one over the
   lower-right part (`x`/`y`/`w`/`h` box).
3. Add a 1–3 pixel highlight on the upper left (`pixel`, color 0 or the
   light color).
4. `outline` in the ink color. It traces only the outside, so shines
   and eye glints stay.
5. Details last: eyes (2×3 ink with a glint pixel), mouths, seams,
   legs, sparkles.

Animate whatever should feel alive: a second frame plus `add_anim_frame`
/ `set_anim`. Examples: a spinning coin (4 frames), a twinkling star,
a fizzing fuse (2 frames, rate 5–12).

Big pictures (dishes, moons, logos) go in a 32×32 sprite, or are split
across 2–4 sprites placed side by side.

## 3. Scenery and layout: tile layers

`create_tile_sheet` (16 cells) then `set_tilemap` with text rows. Tiles
cost no object slots, so be generous:
* **Games**: ground (top edge, fill, deep fill), edge caps at pits,
  platforms, and decor (bushes, flowers, fences, clouds, dithered
  far-away hills, starfields, skylines with lit windows).
* **Apps and pages**: a nav bar fill, rounded card borders (8 pieces)
  with a header strip, pill buttons (6 pieces, 16px tall) and patterned
  panels (dotted grids).
* Use a different `solid` list per sheet: only the cells that block
  movement.
* Fill every scene, the title and game-over screens included. They
  reuse the same backdrop.

## 4. Type and screens

* `headline` for titles, two letters per 32×24 sprite: ink plus a
  shadow in an accent color. Give every game a title screen (headline,
  a one-line how-to, a blinking "PRESS SPACE") and an end screen
  (headline, score, retry prompt).
* Blinking text: a create event `wait 40`; alarm 0 → `hide`, `wait 20`
  on timer 1; alarm 1 → `show`, `wait 40`.
* Buttons in apps: a text object over a pill (label padded with a
  space on each side), with a `mask` covering the pill and a click
  event.
* HUD: an icon sprite plus a number (`X 3`), or a row of hearts. Hearts
  are a template with a var `n` (1, 2, 3) that hides on the `hud`
  message when `lives < my n`. Mark HUD objects `hud` when the scene
  scrolls.

## 5. Structure (same as engine.md)

Pages and levels are scenes; links and buttons are click events that
`goto`; rules and data live in a script that blocks `call`. The script
can draw its own layer in `custom_draw`, guarded by `scene != SC_X`.

## Checklist before saying done

- [ ] Size and theme chosen on purpose.
- [ ] Every sprite is shaded and outlined; at least one animation.
- [ ] Every scene has a tile backdrop; title and end screens exist.
- [ ] Headline type on the title; HUD with icons.
- [ ] `validate` and `check_build` are clean (no warnings either).
