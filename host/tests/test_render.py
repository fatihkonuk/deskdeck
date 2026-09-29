"""Icon loading and cell layout. Text rendering normally needs the macOS system fonts, which CI (Linux)
does not have; tests that draw text use Pillow's built-in font instead."""
import io
import subprocess

import pytest
from PIL import Image, ImageFont

from deskdeck import render
from deskdeck.config import ButtonSpec
from deskdeck.nowplaying import NowPlaying


def png(size: int = 64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), (255, 0, 0, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_valid_icon_file_is_scaled_to_fit(tmp_path):
    path = tmp_path / "icon.png"
    path.write_bytes(png(128))
    img = render._graphic(ButtonSpec("open", "/", "Icon", str(path)), 44)
    assert img is not None
    assert max(img.size) == 44


def test_truncated_icon_file_falls_back(tmp_path):
    path = tmp_path / "icon.png"
    path.write_bytes(png()[:60])
    assert render._graphic(ButtonSpec("open", "/", "Icon", str(path)), 44) is None


def test_non_image_icon_file_falls_back(tmp_path):
    path = tmp_path / "icon.png"
    path.write_text("not an image")
    assert render._graphic(ButtonSpec("open", "/", "Icon", str(path)), 44) is None


def test_app_icon_timeout_is_reported_as_unavailable(tmp_path, monkeypatch):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(render, "APP_ICON_CACHE", tmp_path)
    monkeypatch.setattr(render.subprocess, "run", run)
    with pytest.raises(render.IconUnavailable):
        render._graphic(ButtonSpec("app", "Safari"), 44)


def test_cell_retries_icon_after_timeout(monkeypatch):
    """A timeout at login must not leave the button text-only until the service restarts."""
    monkeypatch.setattr(render, "font", lambda size, weight="Regular": ImageFont.load_default(size))
    render._cell.cache_clear()
    calls = 0

    def app_icon(name):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise render.IconUnavailable("osascript timed out")
        return Image.new("RGBA", (128, 128), (255, 0, 0, 255))

    monkeypatch.setattr(render, "app_icon", app_icon)
    spec = ButtonSpec("app", "RetryTestApp")
    assert len(render.cell(spec)) == 1  # label only
    assert len(render.cell(spec)) == 2  # icon + label
    render.cell(spec)
    assert calls == 2  # the good result is cached
    render._cell.cache_clear()


def test_corrupt_cached_app_icon_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "APP_ICON_CACHE", tmp_path)
    cached = tmp_path / "x.png"
    cached.write_bytes(png()[:60])
    monkeypatch.setattr(render, "app_icon", lambda name: Image.open(cached))
    assert render._graphic(ButtonSpec("app", "Safari"), 44) is None


@pytest.mark.parametrize("error", [Image.DecompressionBombError("too many pixels"), ValueError("Decompressed Data Too Large")])
def test_undecodable_artwork_falls_back_to_placeholder(monkeypatch, error):
    monkeypatch.setattr(render, "font", lambda size, weight="Regular": ImageFont.load_default(size))

    def open_(fp, *args, **kwargs):
        raise error

    monkeypatch.setattr(render.Image, "open", open_)
    img = render.cover(NowPlaying(title="Song", artwork=b"artwork"))
    assert img.size == tuple(render.layout.COVER[2:])


@pytest.mark.parametrize("count", range(2, 25))
def test_current_page_is_always_visible(monkeypatch, count):
    """With many pages the dots are packed closer and finally replaced by an "n/N" label; the current
    page must stay visible (the highlighted dot is the only near-white pixel)."""
    monkeypatch.setattr(render, "font", lambda size, weight="Regular": ImageFont.load_default(size))
    for current in (0, count - 1):
        img = render.page_dots(count, current).convert("L")
        assert img.size == tuple(render.layout.PAGE_DOTS[2:])
        assert img.getextrema()[1] > 200, (count, current)


def test_oversized_icon_file_falls_back(tmp_path, monkeypatch):
    """DecompressionBombError is not an OSError; it must not escape into the session."""
    path = tmp_path / "icon.png"
    path.write_bytes(png(64))
    monkeypatch.setattr(render.Image, "MAX_IMAGE_PIXELS", 100)  # 64×64 is now "too large" (> 2× limit)
    assert render._graphic(ButtonSpec("open", "/", "Icon", str(path)), 44) is None
