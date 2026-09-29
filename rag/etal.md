# ETAL cheat sheet (emitter subset)

Typed DSL compiling to Uxntal. Every expression leaves exactly
`sizeof(type)` bytes; `u8 → u16` widens, narrowing is explicit.

## Devices (declared pages, typed ports)

```ux
device Screen 32 { vector: 2  width: 2  height: 2  x: 2  y: 2  addr: 2  pixel: 1  sprite: 1 }
device Controller 128 { vector: 2  button: 1  key: 1 }
device Mouse 144 { vector: 2  x: 2  y: 2  state: 1 }
device Audio0 48 { vector: 2  addr: 2  length: 2  volume: 1  adsr: 2  pitch: 1 }
```

Reads: `x: u16 = Screen.width;` Writes: `Screen.sprite = 129;`
(2-byte ports trigger device effects — order is semantic).

## Functions, events, vectors

```ux
wrap :: fn(x: u16) -> u16 { return x; }
on_frame :: event() { draw_all(); }   (ends BRK, never JSR-called)
main :: event() { start(); }          (stays in the event loop)
Screen.vector = &on_frame;            (arm the 60Hz handler)
```

No recursion (static locals), no reentrancy. `match` over integer
literals plus trailing `_`. `for i in 0..N` (u16), `while`,
`if/elif/else`. `print("...")` inlines a console loop.

## Memory

Globals live in 256B zero-page. `buffer buf[N]: u8|u16;` is main RAM.
`data tile = [bytes];` is a ROM blob; `&tile` is its address.
Fixed 8×8 2bpp sprites = 16 planar bytes (channel one, then two).

## Input doctrine (hardware facts)

`Controller.key` self-clears after firing — latch it in the controller
vector, consume in the frame. Sub-frame taps are invisible to polling,
so OR-accumulate buttons in the vector. Edge = `cur & (last ^ 255)`.
D-pad mask bits: 16 up, 32 down, 64 left, 128 right. Key codes:
32 space, 27 escape. Mouse state bit 1 = left.
