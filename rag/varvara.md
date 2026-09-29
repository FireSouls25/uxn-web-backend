# Varvara context (verified against uxn2/src/uxn2.c)

## Screen

* 8px-wide sprites; height = bytes ÷ (1 or 2 per row). Sprite byte:
  bit7 = 2bpp, bit6 = layer, bits5-4 = flips, low nibble = blend.
* Blend LUT row 1 is identity `{0,1,2,3}` and opaque — so mode **129**
  (0x81) draws true 4-color 2bpp sprites. Index 0 is NOT transparent
  there; it paints palette 0. (Blend 0 skips index 0 but collapses
  index 1 into palette 0 — wrong for full-color art.)
* No cursor is drawn by the VM — games blit their own (`SDL_ShowCursor`
  off). Coordinates are window pixels: at zoom ≠ 1, hit-tests
  miscalibrate by the zoom factor (upstream behavior).

## Palette

System r/g/b ports hold four 4-bit nibbles each. Color *i* takes
nibble (i==0||i==2 ? 4:0) of byte (i<2 ? low : high); channels expand
by duplication (`0xB → 0xBB`). Demo theme `45163, 32876, 16508`
decodes to grey-blue `#666677`, pale `#bbcccc`, brown `#bb8844`,
black `#000000`. Any theme is legal — that is the whole color story.

## Controller / Mouse

* Button byte bitmask (d-pad above); `key` = last keycode, self-clearing.
* Mouse `x/y` track always, `state` bitmask, scroll ports are one-shot
  deltas with inverted Y (zeroed after the vector fires).

## Audio (4 voices, Audio0–3)

* `pitch` = MIDI note (69 ≈ 441Hz, 0–107 audible, 108+ silent);
  high bit set = one-shot, clear = loop.
* Samples: unsigned 8-bit mono 44100Hz, 128-center. `length` in bytes,
  `volume` byte, `adsr` envelope. Long samples play ~1:1 at middle C.
* Web emulator (uxn5) has no audio device — builds play silently there.

## System / DateTime / File

* `System.r/g/b` theme the palette; `System.vector` boots.
  `metadata` blob carries title/author.
* DateTime ports seed RNG. File pages do host I/O relative to the
  emulator CWD (native: inline in DEO; web: completes via vector).
