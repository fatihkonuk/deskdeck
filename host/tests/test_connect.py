import asyncio

from deskdeck import connect
from deskdeck.config import DeviceConfig
from deskdeck.link import LinkError

CFG = DeviceConfig(host="deskdeck.local", port=7788, token="t", ip=None)


class FakeLink:
    def __init__(self) -> None:
        self.events: asyncio.Queue = asyncio.Queue()
        self.failed = asyncio.get_running_loop().create_future()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def run_briefly(monkeypatch, session, seconds: float = 0.1) -> list[FakeLink]:
    """Runs run_forever against fake links; fails if it returns or raises on its own."""
    links: list[FakeLink] = []

    async def open_link(cfg):
        links.append(FakeLink())
        return links[-1]

    monkeypatch.setattr(connect, "open_link", open_link)
    monkeypatch.setattr(connect, "BACKOFF_MIN_S", 0.001)
    monkeypatch.setattr(connect, "BACKOFF_MAX_S", 0.001)

    async def main():
        task = asyncio.create_task(connect.run_forever(CFG, session))
        await asyncio.sleep(seconds)
        assert not task.done(), task.exception()
        task.cancel()

    asyncio.run(main())
    return links


def test_unexpected_session_error_reconnects(monkeypatch, caplog):
    async def session(link):
        raise RuntimeError("bug in the session")

    links = run_briefly(monkeypatch, session)
    assert len(links) > 1
    assert all(link.closed for link in links[:-1])
    assert "session failed" in caplog.text
    assert "bug in the session" in caplog.text  # traceback included


def test_link_error_reconnects(monkeypatch, caplog):
    async def session(link):
        link.failed.set_exception(LinkError("peer closed the connection"))
        await asyncio.Event().wait()

    links = run_briefly(monkeypatch, session)
    assert len(links) > 1
    assert "link: peer closed the connection" in caplog.text


def test_open_link_error_reconnects(monkeypatch):
    attempts = 0

    async def open_link(cfg):
        nonlocal attempts
        attempts += 1
        raise OSError("cache directory not writable")

    async def session(link):
        raise AssertionError("never reached")

    monkeypatch.setattr(connect, "open_link", open_link)
    monkeypatch.setattr(connect, "BACKOFF_MIN_S", 0.001)
    monkeypatch.setattr(connect, "BACKOFF_MAX_S", 0.001)

    async def main():
        task = asyncio.create_task(connect.run_forever(CFG, session))
        await asyncio.sleep(0.1)
        assert not task.done(), task.exception()
        task.cancel()

    asyncio.run(main())
    assert attempts > 1
