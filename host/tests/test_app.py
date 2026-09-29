import asyncio
import os
import shutil
import struct
import time
from pathlib import Path

import pytest

from deskdeck import app as app_module
from deskdeck import protocol as p
from deskdeck.nowplaying import NowPlaying

EXAMPLE = Path(__file__).resolve().parent.parent / "config.toml.example"


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    shutil.copy(EXAMPLE, path)
    return path


@pytest.fixture
def performed(monkeypatch) -> list[str]:
    """Records actions instead of running them on the Mac."""
    calls: list[str] = []

    async def perform(spec):
        calls.append(spec.arg)

    monkeypatch.setattr(app_module.actions, "perform", perform)
    monkeypatch.setattr(app_module, "VOLUME_REPEAT_S", 0.01)
    return calls


def make_app(config_path: Path) -> app_module.App:
    app = app_module.App(config_path)

    async def draw(*_):
        pass

    app._draw = draw  # the screen is not under test here
    return app


def vol_up_index(app: app_module.App) -> int:
    return next(i for i, spec in enumerate(app.pages[0]) if spec and spec.arg == "vol_up")


def test_volume_repeat_stops_when_session_ends(config_path, performed):
    async def main():
        app = make_app(config_path)
        session = asyncio.create_task(app.session(link=None))
        app.on_event(p.Button(0, vol_up_index(app), p.LONG))
        await asyncio.sleep(0.05)
        assert app._repeat and not app._repeat.done()

        session.cancel()  # what run_forever does when the link drops
        await asyncio.gather(session, return_exceptions=True)
        assert app._repeat is None
        count = len(performed)
        await asyncio.sleep(0.05)
        assert len(performed) == count

    asyncio.run(main())


def test_volume_repeat_is_capped(config_path, performed, monkeypatch):
    monkeypatch.setattr(app_module, "VOLUME_REPEAT_MAX_S", 0.05)

    async def main():
        app = make_app(config_path)
        app.on_event(p.Button(0, vol_up_index(app), p.LONG))
        await asyncio.sleep(0.2)
        assert app._repeat.done()
        # the release that finally arrives must not trigger one more step
        count = len(performed)
        app.on_event(p.Button(0, vol_up_index(app), p.RELEASE))
        await asyncio.sleep(0.02)
        assert len(performed) == count

    asyncio.run(main())


def test_release_runs_action_once(config_path, performed):
    async def main():
        app = make_app(config_path)
        app.on_event(p.Button(0, vol_up_index(app), p.RELEASE))
        await asyncio.sleep(0.02)
        assert performed == ["vol_up"]

    asyncio.run(main())


def touch(path: Path, text: str) -> None:
    """Writes the file with a new mtime, even on filesystems with coarse timestamps."""
    mtime = path.stat().st_mtime + 1
    path.write_text(text)
    os.utime(path, (mtime, mtime))


@pytest.mark.parametrize("text", ['pages = ["x"]', "[[pages]]\nbuttons = [1]", "[[pages]\n"])
def test_reload_keeps_previous_pages_on_malformed_config(config_path, text):
    async def main():
        app = make_app(config_path)
        pages = app.pages
        touch(config_path, text)
        app._reload_pages()
        assert app.pages is pages

    asyncio.run(main())


def test_reload_survives_file_missing_during_save(config_path, tmp_path, monkeypatch):
    load_pages = app_module.config.load_pages

    async def main():
        app = make_app(config_path)
        pages = app.pages
        # stat() saw the new mtime, then the editor's non-atomic save removed the file before the read
        touch(config_path, config_path.read_text())
        monkeypatch.setattr(app_module.config, "load_pages", lambda path: load_pages(tmp_path / "gone.toml"))
        app._reload_pages()
        assert app.pages is pages

    asyncio.run(main())


def test_reload_applies_valid_config(config_path):
    async def main():
        app = make_app(config_path)
        app.page = 2
        touch(config_path, '[[pages]]\nbuttons = [{ app = "Safari" }]\n')
        app._reload_pages()
        assert len(app.pages) == 1
        assert app.page == 0

    asyncio.run(main())


def test_progress_carries_position_at_send_time(config_path, monkeypatch):
    """After a song change the cover and grid blits wait on credit; the position sent in PROGRESS
    must include that delay, since the MCU advances the bar from the moment the frame arrives."""
    blit_delay = 0.3

    async def slow_blit(link, rect, img):
        await asyncio.sleep(blit_delay)

    async def no_grid(*_):
        pass

    monkeypatch.setattr(app_module, "blit", slow_blit)
    monkeypatch.setattr(app_module.render, "cover", lambda np: None)
    monkeypatch.setattr(app_module.render, "time_image", lambda label: None)

    class RecordingLink:
        def __init__(self):
            self.frames: list[bytes] = []

        async def send(self, frame):
            self.frames.append(frame)

    async def main():
        app = app_module.App(config_path)
        app._draw_grid = no_grid
        link = RecordingLink()
        np = NowPlaying(title="Song", duration_s=200.0, elapsed_s=10.0, timestamp=time.time(), rate=1.0,
                        playing=True)
        drawn = {"text": (np.title, np.artist, np.album)}  # text unchanged; the cover still gets blitted
        await app._draw(link, np, time.time(), drawn)
        ((_, payload),) = [f for f in p.Decoder().feed(b"".join(link.frames)) if f[0] == p.PROGRESS]
        (elapsed_ms,) = struct.unpack_from("<I", payload)
        assert elapsed_ms >= (10.0 + blit_delay) * 1000

    asyncio.run(main())


def test_tap_after_capped_repeat_runs_its_action(config_path, performed, monkeypatch):
    """The RELEASE of a long press is lost, the repeat stops at its time limit, then another button is
    tapped: that tap must run its action, not be taken for the end of the long press."""
    monkeypatch.setattr(app_module, "VOLUME_REPEAT_MAX_S", 0.05)

    async def main():
        app = make_app(config_path)
        vol = vol_up_index(app)
        other = next(i for i, spec in enumerate(app.pages[0]) if spec and spec.arg not in ("vol_up", "vol_down"))
        app.on_event(p.Button(0, vol, p.PRESS))
        app.on_event(p.Button(0, vol, p.LONG))
        await asyncio.sleep(0.2)  # RELEASE lost; the cap stops the repeat
        performed.clear()
        app.on_event(p.Button(0, other, p.PRESS))
        app.on_event(p.Button(0, other, p.RELEASE))
        await asyncio.sleep(0.02)
        assert performed == [app.pages[0][other].arg]

    asyncio.run(main())


def test_release_stops_repeat_when_cell_was_emptied(config_path, performed):
    async def main():
        app = make_app(config_path)
        vol = vol_up_index(app)
        app.on_event(p.Button(0, vol, p.PRESS))
        app.on_event(p.Button(0, vol, p.LONG))
        await asyncio.sleep(0.03)
        app.pages = (tuple(None for _ in app.pages[0]),)  # config reload emptied the page
        app.on_event(p.Button(0, vol, p.RELEASE))
        assert app._repeat is None
        count = len(performed)
        await asyncio.sleep(0.05)
        assert len(performed) == count

    asyncio.run(main())


def test_failed_action_is_logged(config_path, monkeypatch, caplog):
    async def perform(spec):
        raise FileNotFoundError("media-control")

    monkeypatch.setattr(app_module.actions, "perform", perform)

    async def main():
        app = make_app(config_path)
        app.on_event(p.Button(0, vol_up_index(app), p.RELEASE))
        await asyncio.sleep(0.02)

    asyncio.run(main())
    assert "action failed" in caplog.text
