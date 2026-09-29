#!/usr/bin/env python3
"""Touch bring-up: starts the touch test on the harness firmware and shows live values.

  python3 tools/touch_ctl.py --start    start drawing mode with the built-in calibration
  python3 tools/touch_ctl.py --cal      recalibrate with 4 targets (copy the values into touch.c)
  python3 tools/touch_ctl.py            current state
  python3 tools/touch_ctl.py --watch    state every 0.5 s (Ctrl-C to quit)

Layout must match touch_probe_t in firmware/src/harness_main.c.
"""
import argparse
import struct
import sys
import time

from lcd_ctl import apply
from swd import read_mem, symbol

MAGIC = 0x48435554
FMT = "<IIHHHBBBBHhhhhhh"
KEYS = ["magic", "samples", "raw_x", "raw_y", "raw_z", "pressed", "state", "cal_valid", "swap_xy",
        "reserved", "raw_x_a", "raw_x_b", "raw_y_a", "raw_y_b", "last_sx", "last_sy"]


def read_touch() -> dict:
    addr, size = symbol("g_touch")
    t = dict(zip(KEYS, struct.unpack(FMT, read_mem(addr, size))))
    if t["magic"] != MAGIC:
        sys.exit(f"Wrong magic ({t['magic']:#x}): stale firmware.")
    return t


def show(t: dict) -> None:
    state = "drawing" if t["state"] == 4 else f"calibration target {t['state'] + 1}/4"
    line = (f"{state:22} raw x={t['raw_x']:4} y={t['raw_y']:4} z={t['raw_z']:4} "
            f"{'PRESSED' if t['pressed'] else '       '} samples={t['samples']}")
    if t["cal_valid"]:
        line += (f" | cal: swap={t['swap_xy']} x {t['raw_x_a']}→{t['raw_x_b']} "
                 f"y {t['raw_y_a']}→{t['raw_y_b']} last=({t['last_sx']},{t['last_sy']})")
    print(line, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--cal", action="store_true")
    ap.add_argument("--watch", action="store_true")
    args = ap.parse_args()
    if args.start or args.cal:
        apply(pattern="touchcal" if args.cal else "touch", redraw=True)
    while True:
        show(read_touch())
        if not args.watch:
            break
        time.sleep(0.5)


if __name__ == "__main__":
    main()
