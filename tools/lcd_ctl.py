#!/usr/bin/env python3
"""Display bring-up: reads/changes the running harness firmware's g_probe settings over SWD.

  python3 tools/lcd_ctl.py                     show the current state
  python3 tools/lcd_ctl.py --madctl 0x28 --invert 1 --pattern lines
  python3 tools/lcd_ctl.py --redraw            re-initialise and redraw with the same settings

Layout must match lcd_probe_t in firmware/src/harness_main.c.
"""
import argparse
import struct
import sys
import time

from swd import openocd, read_mem, symbol

MAGIC = 0x4252504C
FMT = "<IIIBBBBII"
PATTERNS = {"card": 0, "lines": 1, "gradients": 2, "rotations": 3, "touchdiag": 4, "touch": 5, "touchcal": 6}
OFF_REQ, OFF_MADCTL, OFF_INVERT, OFF_PATTERN = 4, 12, 13, 14


def read_probe(addr: int, size: int) -> dict:
    vals = struct.unpack(FMT, read_mem(addr, size))
    keys = ["magic", "req_seq", "done_seq", "madctl", "invert", "pattern", "reserved", "sysclk_hz", "fill_us"]
    p = dict(zip(keys, vals))
    if p["magic"] != MAGIC:
        sys.exit(f"Wrong magic ({p['magic']:#x}): the harness firmware is not running or the ELF is stale.")
    return p


def show(p: dict) -> None:
    pat = {v: k for k, v in PATTERNS.items()}.get(p["pattern"], p["pattern"])
    busy = "" if p["req_seq"] == p["done_seq"] else "  (drawing)"
    print(f"madctl=0x{p['madctl']:02X} invert={p['invert']} pattern={pat} "
          f"sysclk={p['sysclk_hz'] / 1e6:.0f} MHz full-screen fill={p['fill_us'] / 1000:.1f} ms{busy}")


def apply(madctl: int | None = None, invert: int | None = None, pattern: str | None = None,
          redraw: bool = False) -> dict:
    """Writes the given fields, triggers a redraw if needed and waits for it; returns the final state."""
    addr, size = symbol("g_probe")
    p = read_probe(addr, size)

    writes = []
    if madctl is not None:
        writes.append(f"mwb {addr + OFF_MADCTL:#x} {madctl}")
    if invert is not None:
        writes.append(f"mwb {addr + OFF_INVERT:#x} {invert}")
    if pattern is not None:
        writes.append(f"mwb {addr + OFF_PATTERN:#x} {PATTERNS[pattern]}")
    if writes or redraw:
        # req_seq is written last, so the firmware sees the other fields fully updated.
        writes.append(f"mww {addr + OFF_REQ:#x} {p['req_seq'] + 1}")
        openocd(*writes)
        for _ in range(20):
            time.sleep(0.5)
            p = read_probe(addr, size)
            if p["req_seq"] == p["done_seq"]:
                break
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--madctl", type=lambda s: int(s, 0))
    ap.add_argument("--invert", type=int, choices=[0, 1])
    ap.add_argument("--pattern", choices=PATTERNS)
    ap.add_argument("--redraw", action="store_true")
    args = ap.parse_args()
    show(apply(args.madctl, args.invert, args.pattern, args.redraw))


if __name__ == "__main__":
    main()
