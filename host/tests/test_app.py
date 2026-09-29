import asyncio
import os
import shutil
from pathlib import Path

import pytest

from deskdeck import app as app_module
from deskdeck import protocol as p

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
