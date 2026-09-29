"""Renders the pieces that go to the screen with Pillow. Text and artwork are rendered on the Mac so
any script (non-Latin, diacritics, emoji) works and no fonts live in the MCU's flash. Everything has a
black background and fills its whole region: a new piece overwrites the old one, no separate clear."""
import hashlib
import io
import logging
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import layout
from .config import ButtonSpec
from .nowplaying import NowPlaying

log = logging.getLogger(__name__)

BG = (0, 0, 0)
FG = (240, 240, 240)
DIM = (160, 160, 170)
FAINT = (110, 110, 120)
ICON = (225, 225, 230)

SF = "/System/Library/Fonts/SFNS.ttf"
FALLBACK = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
EMOJI = "/System/Library/Fonts/Apple Color Emoji.ttc"
EMOJI_SIZE = 160  # the color emoji font only renders at fixed sizes; draw large, then scale down
ICON_SUPERSAMPLE = 4
APP_ICON_CACHE = Path.home() / "Library/Caches/deskdeck/icons"


@lru_cache(maxsize=16)
def font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    try:
        f = ImageFont.truetype(SF, size)
        f.set_variation_by_name(weight.encode())
        return f
    except (OSError, ValueError):
        return ImageFont.truetype(FALLBACK, size)


def _width(text: str, f: ImageFont.FreeTypeFont) -> float:
    return f.getlength(text)


def ellipsize(text: str, f: ImageFont.FreeTypeFont, width: int) -> str:
    if _width(text, f) <= width:
        return text
    while text and _width(text + "…", f) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int, max_lines: int) -> list[str]:
    """Wraps word by word; the last line is ellipsized if the text does not fit."""
    lines: list[str] = []
    words = text.split()
    while words and len(lines) < max_lines:
        line = words.pop(0)
        while words and _width(line + " " + words[0], f) <= width:
            line += " " + words.pop(0)
        lines.append(line)
    if words:
        lines[-1] = ellipsize(lines[-1] + " " + " ".join(words), f, width)
    return [ellipsize(line, f, width) for line in lines]


def _strip(rect: tuple[int, int, int, int], text: str, f: ImageFont.FreeTypeFont, color) -> Image.Image:
    _, _, w, h = rect
    img = Image.new("RGB", (w, h), BG)
    if text:
        ImageDraw.Draw(img).text((0, h // 2), text, font=f, fill=color, anchor="lm")
    return img


TextLine = tuple[tuple[int, int, int, int], str, ImageFont.FreeTypeFont, tuple[int, int, int]]


def text_lines(np: NowPlaying) -> list[TextLine]:
    """Title (1-2 lines), artist, album: (rect, text, font, color)."""
    w = layout.TITLE_LINES[0][2]
    title_font, artist_font, album_font = font(24, "Bold"), font(19), font(15)
    if np.empty:
        title, title_color = ["Nothing playing", ""], FAINT
    else:
        title, title_color = wrap(np.title, title_font, w, 2) + ["", ""], FG
    out: list[TextLine] = [(rect, line, title_font, title_color) for rect, line in zip(layout.TITLE_LINES, title)]
    out.append((layout.ARTIST_LINE, ellipsize(np.artist, artist_font, w), artist_font, DIM))
    out.append((layout.ALBUM_LINE, ellipsize(np.album, album_font, w), album_font, FAINT))
    return out


def text_image(line: TextLine) -> Image.Image:
    """Renders the line only as wide as its text; the rest is cleared with FILL (fewer bytes)."""
    rect, text, f, color = line
    w = min(rect[2], int(f.getlength(text)) + 2)
    return _strip((0, 0, w, rect[3]), text, f, color)


def fmt_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def time_label(np: NowPlaying, now: float | None = None) -> str:
    if np.empty or not np.duration_s:
        return ""
    return f"{fmt_time(np.elapsed_now(now))} / {fmt_time(np.duration_s)}"


def time_image(label: str) -> Image.Image:
    return _strip(layout.TIME_TEXT_BLIT, label, font(13, "Medium"), DIM)


def cover(np: NowPlaying) -> Image.Image:
    size = layout.COVER[2:]
    if np.artwork:
        try:
            return ImageOps.fit(Image.open(io.BytesIO(np.artwork)).convert("RGB"), size, Image.LANCZOS)
        except OSError as e:
            log.warning("could not open artwork: %s", e)
    # No artwork: dark square with a note in the middle
    img = Image.new("RGB", size, (28, 28, 34))
    d = ImageDraw.Draw(img)
    d.text((size[0] // 2, size[1] // 2), "♪", font=font(64), fill=(90, 90, 100), anchor="mm")
    return img


def icon(kind: str) -> Image.Image:
    """Vector button icon (no font needed), drawn 4x larger and scaled down."""
    _, _, w, h = layout.icon_rect(0)
    s = ICON_SUPERSAMPLE
    img = Image.new("RGB", (w * s, h * s), BG)
    d = ImageDraw.Draw(img)
    cx, cy, r = w * s / 2, h * s / 2, 17 * s

    def tri(points):
        d.polygon([(cx + x * r, cy + y * r) for x, y in points], fill=ICON)

    def box(x0, y0, x1, y1):
        d.rectangle((cx + x0 * r, cy + y0 * r, cx + x1 * r, cy + y1 * r), fill=ICON)

    if kind == "play":
        tri([(-0.6, -1), (-0.6, 1), (1, 0)])
    elif kind == "pause":
        box(-0.7, -0.95, -0.2, 0.95)
        box(0.2, -0.95, 0.7, 0.95)
    elif kind in ("next", "prev"):
        m = 1 if kind == "next" else -1
        tri([(-0.95 * m, -0.8), (-0.95 * m, 0.8), (0.25 * m, 0)])
        tri([(-0.2 * m, -0.8), (-0.2 * m, 0.8), (1.0 * m, 0)])
        x0, x1 = sorted((1.0 * m, 1.2 * m))
        box(x0, -0.8, x1, 0.8)
    elif kind in ("vol_down", "vol_up", "mute"):
        box(-1.1, -0.35, -0.65, 0.35)
        tri([(-0.65, -0.35), (-0.15, -0.85), (-0.15, 0.85), (-0.65, 0.35)])
        if kind == "mute":
            for y0, y1 in ((-0.4, 0.4), (0.4, -0.4)):
                d.line((cx + 0.3 * r, cy + y0 * r, cx + 1.05 * r, cy + y1 * r), fill=ICON, width=int(0.2 * r))
        else:
            box(0.25, -0.1, 1.0, 0.1)
        if kind == "vol_up":
            box(0.525, -0.375, 0.725, 0.375)
    return img.resize((w, h), Image.LANCZOS)


# ---- shortcut cells ----

Part = tuple[int, int, Image.Image]  # dx, dy relative to the icon area's (layout.icon_rect) top-left, image

GRAPHIC = 44  # icon size in a cell with a label
GRAPHIC_ALONE = 58
LABEL_H = 16
LABEL_GAP = 4

# App icon from NSWorkspace (including apps that use Assets.car) → PNG
_APP_ICON_JS = """
ObjC.import('AppKit');
function run(argv) {
  var ws = $.NSWorkspace.sharedWorkspace;
  var path = ws.fullPathForApplication(argv[0]);
  if (!path || path.isNil()) throw new Error('application not found: ' + argv[0]);
  var img = ws.iconForFile(path);
  img.setSize({width: 128, height: 128});
  var rep = $.NSBitmapImageRep.alloc.initWithCGImage(img.CGImageForProposedRectContextHints(null, $(), $()));
  rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $()).writeToFileAtomically(argv[1], true);
}
"""


class IconUnavailable(Exception):
    """The icon could not be fetched this time, but a later attempt may succeed."""


def app_icon(name: str) -> Image.Image | None:
    """The application's Dock icon, cached on disk. None if there is no such app; IconUnavailable if
    fetching it timed out (e.g. a busy system right after login)."""
    path = APP_ICON_CACHE / f"{hashlib.sha1(name.encode()).hexdigest()[:16]}.png"
    if not path.exists():
        APP_ICON_CACHE.mkdir(parents=True, exist_ok=True)
        try:
            r = subprocess.run(["osascript", "-l", "JavaScript", "-e", _APP_ICON_JS, name, str(path)],
                               capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            raise IconUnavailable(f"could not get icon for {name}: osascript timed out") from None
        if r.returncode or not path.exists():
            log.warning("could not get icon for %s: %s", name, r.stderr.strip())
            return None
    return Image.open(path)


def _symbol(text: str) -> Image.Image | None:
    """Color emoji or any other symbol/text, on a transparent background, cropped to its content."""
    if any(ord(c) > 0x2000 for c in text):
        f = ImageFont.truetype(EMOJI, EMOJI_SIZE)
        img = Image.new("RGBA", (EMOJI_SIZE * 2 * len(text), EMOJI_SIZE * 2), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((0, 0), text, font=f, embedded_color=True)
        if img.getbbox():
            return img
    img = Image.new("RGBA", (64 * len(text), 80), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, 40), text, font=font(56, "Bold"), fill=ICON, anchor="lm")
    return img if img.getbbox() else None


def _graphic(spec: ButtonSpec, size: int) -> Image.Image | None:
    try:
        if spec.icon:
            p = Path(spec.icon).expanduser()
            src = Image.open(p) if p.is_file() else _symbol(spec.icon)
        elif spec.action == "app":
            src = app_icon(spec.arg)
        else:
            return None
        if src is None:
            return None
        src = src.convert("RGBA")  # Image.open() is lazy: a truncated or corrupt file fails here
    except OSError as e:
        log.warning("could not load icon for %s: %s", spec.name, e)
        return None
    bbox = src.getchannel("A").getbbox()
    if bbox is None:
        return None
    src = ImageOps.contain(src.crop(bbox), (size, size), Image.LANCZOS)
    out = Image.new("RGB", src.size, BG)
    out.paste(src, mask=src.getchannel("A"))
    return out


def _label(text: str, f: ImageFont.FreeTypeFont, width: int, height: int) -> Image.Image:
    text = ellipsize(text, f, width)
    w = min(width, int(f.getlength(text)) + 2)
    return _strip((0, 0, w, height), text, f, FG)


def cell(spec: ButtonSpec) -> tuple[Part, ...]:
    """Cell content as separate parts: the icon area is first filled with black, then only these are
    sent (the full 106×70 area is ~15 KB, the parts ~5 KB). When an icon cannot be fetched right now
    the cell is drawn as text and not cached, so the next redraw tries the icon again."""
    try:
        return _cell(spec)
    except IconUnavailable as e:
        log.warning("%s; showing the label for now", e)
        return _text_cell(spec)


def _text_cell(spec: ButtonSpec) -> tuple[Part, ...]:
    """Text only: up to two lines, centred."""
    _, _, w, h = layout.icon_rect(0)
    f = font(17, "Semibold")
    lines = wrap(spec.name, f, w - 4, 2)
    top = (h - 22 * len(lines)) // 2
    parts = []
    for i, line in enumerate(lines):
        img = _label(line, f, w - 4, 22)
        parts.append(((w - img.width) // 2, top + 22 * i, img))
    return tuple(parts)


@lru_cache(maxsize=64)
def _cell(spec: ButtonSpec) -> tuple[Part, ...]:
    _, _, w, h = layout.icon_rect(0)
    if spec.action == "media" and not spec.icon:
        img = icon(spec.arg)
        x0, y0, x1, y1 = img.getbbox() or (0, 0, 1, 1)
        return ((x0, y0, img.crop((x0, y0, x1, y1))),)
    label = spec.label or (spec.arg if spec.action in ("app", "shortcut") else "")
    graphic = _graphic(spec, GRAPHIC if label else GRAPHIC_ALONE)
    if graphic is None:
        return _text_cell(spec)  # label or spec.name is always spec.name
    if not label:
        return (((w - graphic.width) // 2, (h - graphic.height) // 2, graphic),)
    top = (h - GRAPHIC - LABEL_GAP - LABEL_H) // 2
    text = _label(label, font(14, "Medium"), w - 2, LABEL_H)
    return (((w - graphic.width) // 2, top + (GRAPHIC - graphic.height) // 2, graphic),
            ((w - text.width) // 2, top + GRAPHIC + LABEL_GAP, text))


def page_dots(count: int, current: int) -> Image.Image:
    """Right-aligned page dots; empty when there is only one page."""
    _, _, w, h = layout.PAGE_DOTS
    s = ICON_SUPERSAMPLE
    img = Image.new("RGB", (w * s, h * s), BG)
    if count > 1:
        d = ImageDraw.Draw(img)
        step, r = 12 * s, 3 * s
        for i in range(count):
            cx = w * s - r - 2 * s - (count - 1 - i) * step
            d.ellipse((cx - r, h * s / 2 - r, cx + r, h * s / 2 + r), fill=FG if i == current else FAINT)
    return img.resize((w, h), Image.LANCZOS)
