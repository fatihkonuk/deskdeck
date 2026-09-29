import asyncio

import pytest

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


def test_stale_cached_ip_falls_back_to_mdns(monkeypatch, tmp_path):
    """The cached IP now belongs to a host that accepts TCP but never completes HELLO."""
    cache = tmp_path / "device_ip"
    cache.write_text("192.0.2.1")
    opened = fake_network(monkeypatch, cache, resolved="192.0.2.2", hello_ok={"192.0.2.2"})
    asyncio.run(connect.open_link(CFG))
    assert opened == ["192.0.2.1", "192.0.2.2"]
    assert cache.read_text() == "192.0.2.2"


def fake_network(monkeypatch, cache, *, resolved="192.0.2.2", hello_ok=()):
    """Patches TCP, mDNS and HELLO. Returns the list of addresses a connection was opened to."""
    opened: list[str] = []

    async def open_tcp(ip, port):
        opened.append(ip)
        return None, None

    async def resolve(host):
        if resolved is None:
            raise TimeoutError
        return resolved

    async def hello(self, token=b""):
        if opened[-1] not in hello_ok:
            raise LinkError("no HELLO_ACK")

    async def no_status(ip):
        pass

    monkeypatch.setattr(connect, "IP_CACHE", cache)
    monkeypatch.setattr(connect, "_open", open_tcp)
    monkeypatch.setattr(connect, "_resolve", resolve)
    monkeypatch.setattr(connect, "_log_bridge_status", no_status)
    monkeypatch.setattr(connect.Link, "__init__", lambda self, reader, writer: None)
    monkeypatch.setattr(connect.Link, "open", hello)
    return opened


def test_hello_timeout_keeps_cached_ip(monkeypatch, tmp_path):
    """One HELLO timeout on a flaky link must not make every later reconnect depend on mDNS."""
    cache = tmp_path / "device_ip"
    cache.write_text("192.0.2.1")
    fake_network(monkeypatch, cache, resolved=None)
    with pytest.raises(LinkError, match="192.0.2.1: no HELLO_ACK"):
        asyncio.run(connect.open_link(CFG))
    assert cache.read_text() == "192.0.2.1"


def test_address_that_failed_is_not_tried_twice(monkeypatch, tmp_path):
    cache = tmp_path / "device_ip"
    cache.write_text("192.0.2.1")
    opened = fake_network(monkeypatch, cache, resolved="192.0.2.1")  # mDNS agrees with the cache
    with pytest.raises(LinkError):
        asyncio.run(connect.open_link(CFG))
    assert opened == ["192.0.2.1"]


def test_fixed_ip_does_not_fall_back_to_mdns(monkeypatch, tmp_path):
    opened = fake_network(monkeypatch, tmp_path / "device_ip")
    cfg = DeviceConfig(host="deskdeck.local", port=7788, token="t", ip="192.0.2.9")
    with pytest.raises(LinkError, match="192.0.2.9: no HELLO_ACK"):
        asyncio.run(connect.open_link(cfg))
    assert opened == ["192.0.2.9"]
    assert not (tmp_path / "device_ip").exists()


def test_unexpected_error_propagates_when_retry_is_off(monkeypatch):
    async def open_link(cfg):
        return FakeLink()

    async def session(link):
        raise FileNotFoundError("missing.png")

    monkeypatch.setattr(connect, "open_link", open_link)
    with pytest.raises(FileNotFoundError):
        asyncio.run(connect.run_forever(CFG, session, retry_unexpected=False))


@pytest.mark.parametrize("content", [b"\xff\xfe not utf-8", b"not an address", None])
def test_unusable_cache_falls_back_to_mdns(monkeypatch, tmp_path, content):
    cache = tmp_path / "device_ip"
    if content is None:
        cache.mkdir()  # a directory where the file should be
    else:
        cache.write_bytes(content)
    opened = fake_network(monkeypatch, cache, resolved="192.0.2.2", hello_ok={"192.0.2.2"})
    asyncio.run(connect.open_link(CFG))
    assert opened == ["192.0.2.2"]
