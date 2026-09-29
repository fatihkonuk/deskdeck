import asyncio
import struct
import time
from dataclasses import astuple

import pytest

from deskdeck import link as link_module
from deskdeck import protocol as p
from deskdeck.link import HELLO_TIMEOUT_S, Link, LinkError


class ResetWriter:
    """A StreamWriter whose peer has reset the connection."""

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        raise ConnectionResetError("reset by peer")

    def close(self) -> None:
        pass


def test_write_error_is_reported_as_link_error():
    async def main():
        link = Link(asyncio.StreamReader(), ResetWriter())
        link.ack = p.HelloAck(proto=1, firmware=1, width=480, height=320, window=7673, max_payload=512)
        link._ready.set()
        with pytest.raises(LinkError, match="reset by peer"):
            await link.send(p.encode(p.PING))
        assert link.failed.done()
        with pytest.raises(LinkError, match="link is down"):
            await link.send(p.encode(p.PING))

    asyncio.run(main())


def test_hello_write_error_is_reported_as_link_error():
    async def main():
        link = Link(asyncio.StreamReader(), ResetWriter())
        with pytest.raises(LinkError, match="reset by peer during HELLO.*token"):
            await link.hello(b"token")
        link.failed.exception()  # retrieved; avoids a "never retrieved" warning

    asyncio.run(main())


class NullWriter:
    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_hello_fails_fast_when_peer_closes():
    """The bridge closes the connection on a wrong token: report that right away, with a hint."""
    async def main():
        reader = asyncio.StreamReader()
        reader.feed_eof()
        link = Link(reader, NullWriter())
        start = time.monotonic()
        with pytest.raises(LinkError, match="peer closed the connection during HELLO.*token"):
            await link.open(b"wrong")
        assert time.monotonic() - start < HELLO_TIMEOUT_S / 2

    asyncio.run(main())


def test_hello_times_out_without_ack(monkeypatch):
    monkeypatch.setattr(link_module, "HELLO_TIMEOUT_S", 0.05)

    async def main():
        link = Link(asyncio.StreamReader(), NullWriter())  # connected, but nothing ever answers
        with pytest.raises(LinkError, match="no HELLO_ACK"):
            await link.open(b"token")

    asyncio.run(main())


def test_hello_ack_opens_session():
    ack = p.HelloAck(proto=1, firmware=3, width=480, height=320, window=7673, max_payload=512)

    async def main():
        reader = asyncio.StreamReader()
        reader.feed_data(p.encode(p.HELLO_ACK, struct.pack("<BHHHHH", *astuple(ack))))
        link = Link(reader, NullWriter())
        assert await link.open(b"token") == ack
        await link.close()

    asyncio.run(main())
