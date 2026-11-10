# ETAL language reference (what scripts are written in)

ETAL is a typed language that compiles to Uxntal for the Uxn VM.
Every expression leaves exactly `sizeof(type)` bytes on the stack.
Source of truth: `uxn-dsl/docs/language/*.md`. When unsure, write the
code, run `check_build`, and fix what the compiler says.

## Types

`u8` (1 byte), `u16` (2 bytes, big-endian), `bool` (0/1), `i8`/`i16`
(two's complement; no signed `/` or `%`), `u8 mod N` / `u16 mod N`
(every store wraps into [0,N)), `[N] T` fixed arrays, `&T` addresses,
structs. Literals take the smallest type that fits (0–255 is `u8`).
Arithmetic and bitwise ops give `u8` only when BOTH sides are `u8`,
otherwise `u16`. Comparisons give `bool`. `&&`/`||` are bitwise and
evaluate both sides (no short-circuit).

**No implicit narrowing.** `u8 → u16` widens automatically, but a
`u16` into a `u8` is a type error. The narrowing bridge is a mod type:

```ux
to8 :: fn(v: u16) -> u8 {
    t: u16 mod 256 = v;
    return t;
}
```

Masking (`v & 255`) still yields `u16`, so it does not narrow.
There are no casts and a `u16` never becomes a pointer.

## Declarations (the second symbol decides mutability)

```ux
x: u16;              ( mutable, zero-page )
x: u16 = 5;          ( mutable with initializer, runs at boot )
n: u16 = 0;          ( prefer explicit types over `n := 0` )
LIMIT : u16 : 20;    ( typed constant with storage )
WIDTH :: 20;         ( free constant: the label IS the value )
buffer board[64]: u8;     ( main RAM array, global only )
grid: [16] u8;            ( small zero-page array )
data tile = [ 0x3c, 66, 129 ];   ( ROM bytes; &tile is its address; tile[i] reads )
Point :: struct { x: u16; y: u8; };   buffer pts[32]: Point;   pts[i].x = 4;
group pos: u8 { x: u8  y: u8 }        ( zero-page struct, .pos/x )
meta { title: "Game", author: "me" }  ( once per program )
import "rules.ux"                      ( splice another file; include-once )
```

Only GLOBAL arrays/buffers are indexable (no local arrays, no array
literals, no bounds checks). Arrays copy whole with `=` when type and
length match; otherwise they never touch the stack.

## Functions, events, macros

```ux
add :: fn(a: u16, b: u16) -> u16 { return a + b; }
on_frame :: event() { step(); }     ( a vector handler: ends with BRK )
macro clamp8(v: u16) -> u16 { return v & 255; }   ( inlined, type-checked )
```

* Valued fns MUST `return expr;` on every path (falling off the end
  returns garbage).
* Arguments widen `u8 → u16` at the call site; arity is checked;
  call order is free (signatures are collected first).
* **No recursion and no reentrancy**: params and locals are static
  zero-page bytes. A fn calling itself corrupts its own locals.
* Calling a valued fn as a bare statement warns; write `_ = f();`.
* Events return with bare `return;` only.

## Statements

`if c { } elif c { } else { }`, `while c { }`,
`for i in 0..n { }` (half-open; `i` is always u16; a pure bound is read
once), `match v { 0 => { } 1 => { } _ => { } }` (integer literals,
lowers to an if-chain), `x += 1;` (all compound ops), `goto l;` /
`label l;` (same fn), `assert c;`, `rpush x;` / `rpop;` (return stack),
`raw { TAL }` (escape hatch; unchecked). Comments are `( … )`. Strings exist only as literals for `print("…")`. Escapes are
`\n \t \\ \"`.

## Devices

Ports are typed: `Screen.x = 10;`, `b: u8 = Controller.button;`.
Writing a sprite reads `addr/x/y/auto` at the moment `sprite` is
written, so set those first. The engine declares every Varvara device
(System, Console, Screen, Controller, Mouse, Audio0-3, DateTime).
Never redeclare one.

## Budgets: these are the real failure modes

* **Zero page: 256 bytes.** Every global scalar, every fn param and
  every local takes bytes for the whole program. The engine already
  uses part of it. Prefer `buffer x[1]: u16;` (RAM, read `x[0]`) for
  state, and reuse a few scratch globals instead of naming many locals.
  The error is `zero-page overflow`.
* **Label dictionary (`Symbols exceeded`).** Every fn, data blob,
  buffer and branch is a label. In big programs, use fewer and shorter
  fns, flatten nested ifs with `&&`, and prefer one sprite sheet over
  many sprites.
* **References per fn (~1200)**: split giant fns.
* Unused `data` or globals give warnings. Delete them.
* Drifblim rejects some label names that look like hex (`a1`, `f66`)
  and `face`. Use distinctive names.

## Input facts

`Controller.key` self-clears right after its vector fires, so polling
in the frame never sees it; latch it in a key vector. The engine
already does this, so engine code reads input through events or the
`held` condition. D-pad bits: 1 A, 2 B, 4 select, 8 start, 16 up,
32 down, 64 left, 128 right. Key codes: 32 space, 27 escape, 13 enter.
Mouse `state & 1` is the left button.

## Common compiler errors → fix

* `type mismatch: u16 vs u8` → widen the target to u16, or narrow
  with `to8` above.
* `unknown identifier` → declare it (globals before use in that file)
  or `import` the module that has it.
* `discarded return value` → `_ = f();`.
* `zero-page overflow` → move scalars into `buffer name[1]` and cut
  locals/params.
* `Symbols exceeded` / `References exceeded` → fewer labels, split fns.
* `duplicate definition` → the name is already declared (in another
  script, a library module, or the engine; see reserved names in
  engine.md).
