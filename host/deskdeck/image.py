"""Converts Pillow images to the big-endian RGB565 bytes the display expects."""
from PIL import Image, ImageChops


def to_rgb565(img: Image.Image) -> bytes:
    """2 bytes per pixel: RRRRRGGG GGGBBBBB. The bit fields do not overlap, so ImageChops.add can
    stand in for OR (it never overflows); runs entirely inside Pillow, no Python loop."""
    r, g, b = img.convert("RGB").split()
    hi = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    lo = ImageChops.add(g.point(lambda v: (v << 3) & 0xE0), b.point(lambda v: v >> 3))
    return Image.merge("LA", (hi, lo)).tobytes()
