"""Converts Pillow images to the big-endian RGB565 bytes the display expects; text drawing helpers."""
from pathlib import Path

from PIL import Image, ImageChops, ImageFont

# macOS fonts with broad Unicode coverage, tried in order.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def to_rgb565(img: Image.Image) -> bytes:
    """2 bytes per pixel: RRRRRGGG GGGBBBBB. The bit fields do not overlap, so ImageChops.add can
    stand in for OR (it never overflows); runs entirely inside Pillow, no Python loop."""
    r, g, b = img.convert("RGB").split()
    hi = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    lo = ImageChops.add(g.point(lambda v: (v << 3) & 0xE0), b.point(lambda v: v >> 3))
    return Image.merge("LA", (hi, lo)).tobytes()


def rgb565(r: int, g: int, b: int) -> int:
    return (r & 0xF8) << 8 | (g & 0xFC) << 3 | b >> 3


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError("no suitable font found: " + ", ".join(FONT_CANDIDATES))
