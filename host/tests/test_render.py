"""Icon loading only: text rendering needs the macOS system fonts, which CI (Linux) does not have."""
import io
import subprocess

from PIL import Image

from deskdeck import render
from deskdeck.config import ButtonSpec


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


def test_app_icon_timeout_falls_back(tmp_path, monkeypatch):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(render, "APP_ICON_CACHE", tmp_path)
    monkeypatch.setattr(render.subprocess, "run", run)
    assert render._graphic(ButtonSpec("app", "Safari"), 44) is None


def test_corrupt_cached_app_icon_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "APP_ICON_CACHE", tmp_path)
    cached = tmp_path / "x.png"
    cached.write_bytes(png()[:60])
    monkeypatch.setattr(render, "app_icon", lambda name: Image.open(cached))
    assert render._graphic(ButtonSpec("app", "Safari"), 44) is None
