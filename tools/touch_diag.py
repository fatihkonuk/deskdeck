#!/usr/bin/env python3
"""Runs the touch wiring diagnostic and works out the XP/XM/YP/YM mapping.

Usage (from the project root, with the harness firmware flashed; don't touch the screen):
  python3 tools/touch_diag.py
Layout must match touch_diag_t in firmware/src/touch_diag.c.
"""
import struct
import sys

from lcd_ctl import apply
from swd import read_mem, symbol

MAGIC = 0x47414454
LCD_NAMES = {0x00: "PA0 (D0)", 0x01: "PA1 (D1)", 0x06: "PA6 (D6)", 0x07: "PA7 (D7)",
             0x10: "PB0 (CS)", 0x11: "PB1 (RS)", 0x16: "PB6 (WR)"}
MAPPINGS = {
    "A": {frozenset({0x11, 0x00}), frozenset({0x10, 0x01})},  # XM-XP, YP-YM
    "B": {frozenset({0x11, 0x06}), frozenset({0x16, 0x07})},
}


def main() -> None:
    apply(pattern="touchdiag", redraw=True)
    addr, size = symbol("g_touch_diag")
    raw = read_mem(addr, size)
    magic, runs, count = struct.unpack_from("<IIB", raw)
    if magic != MAGIC:
        sys.exit(f"Wrong magic ({magic:#x}): the diagnostic did not run.")
    ids = raw[9:9 + count]
    masks = raw[9 + count:9 + 2 * count]

    pairs = set()
    print(f"Diagnostic run: {runs}")
    for i, pid in enumerate(ids):
        lows = [ids[j] for j in range(count) if masks[i] >> j & 1]
        print(f"  {LCD_NAMES[pid]:9} LOW → reads LOW: {', '.join(LCD_NAMES[x] for x in lows) or '-'}")
        pairs |= {frozenset({pid, x}) for x in lows}

    # A connection should show up in both directions; a one-way pair is suspicious.
    one_way = [p for p in pairs if not all(
        masks[list(ids).index(a)] >> list(ids).index(b) & 1 for a in p for b in p if a != b)]
    print("\nConnected pairs:", ", ".join(" – ".join(LCD_NAMES[x] for x in sorted(p)) for p in pairs) or "none")
    if one_way:
        print("Warning: some pairs only show up in one direction, the result is unreliable.")
    match = [name for name, want in MAPPINGS.items() if want == pairs]
    print("Result: mapping", match[0] if match else "unknown (matches neither A nor B)")


if __name__ == "__main__":
    main()
