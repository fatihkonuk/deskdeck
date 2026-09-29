import asyncio

import pytest

from deskdeck import protocol as p
from deskdeck.link import Link, LinkError


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
        with pytest.raises(LinkError, match="reset by peer"):
            await link.hello(b"token")
        link.failed.exception()  # retrieved; avoids a "never retrieved" warning

    asyncio.run(main())
