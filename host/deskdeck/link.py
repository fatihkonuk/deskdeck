"""Session with the MCU: HELLO, byte-based credit, PING, events. Transport independent
(asyncio StreamReader/StreamWriter: serial port or TCP)."""
import asyncio
import logging
import struct
import time

from . import protocol as p

log = logging.getLogger(__name__)

PING_INTERVAL_S = 1.0
STALL_TIMEOUT_S = 5.0  # lwIP retransmits over Wi-Fi can take 1-3 s; a real disconnect shows up as a TCP error right away
HELLO_TIMEOUT_S = 3.0


class LinkError(Exception):
    pass


class Link:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._decoder = p.Decoder()
        self._ack: asyncio.Future[p.HelloAck] | None = None
        self._credit = asyncio.Event()
        self._ready = asyncio.Event()  # sends wait while HELLO is in progress
        self._tasks: list[asyncio.Task] = []
        self.events: asyncio.Queue[p.Button | p.Log] = asyncio.Queue()
        self.failed: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.ack: p.HelloAck | None = None
        self.sent = 0
        self.consumed = 0
        self._last_progress = time.monotonic()

    @property
    def in_flight(self) -> int:
        return (self.sent - self.consumed) & 0xFFFFFFFF

    async def open(self, token: bytes = b"") -> p.HelloAck:
        self._tasks.append(asyncio.create_task(self._rx_loop()))
        try:
            await self.hello(token)
        except LinkError:
            await self.close()
            raise
        self._tasks.append(asyncio.create_task(self._ping_loop()))
        return self.ack

    async def hello(self, token: bytes = b"") -> p.HelloAck:
        """(Re)establishes the session: the MCU clears the screen and the counters are reset."""
        self._ready.clear()
        self._ack = asyncio.get_running_loop().create_future()
        self._writer.write(p.hello(token))  # not counted against the credit
        await self._drain()
        try:
            self.ack = await asyncio.wait_for(asyncio.shield(self._ack), HELLO_TIMEOUT_S)
        except TimeoutError:
            raise LinkError("no HELLO_ACK") from None
        self.sent = self.consumed = 0
        self._last_progress = time.monotonic()
        self._ready.set()
        return self.ack

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._writer.close()

    async def send(self, frame: bytes) -> None:
        """Waits until the frame fits in the credit window; LinkError if the link stalls."""
        if self.failed.done():
            raise LinkError("link is down")
        while True:
            await self._ready.wait()
            if self.in_flight == 0:
                self._last_progress = time.monotonic()
            if self.in_flight + len(frame) <= self.ack.window:
                break
            self._credit.clear()
            try:
                await asyncio.wait_for(self._credit.wait(), STALL_TIMEOUT_S)
            except TimeoutError:
                self._fail(LinkError(f"credit stalled ({self.in_flight} B in flight)"))
                raise self.failed.exception() from None
        # No await between the last check and the write: a HELLO cannot slip in between.
        self._writer.write(frame)
        self.sent = (self.sent + len(frame)) & 0xFFFFFFFF
        await self._drain()

    async def _drain(self) -> None:
        """drain() raises the transport's own OSError (e.g. connection reset); report it as a LinkError
        so callers only have to handle one exception type."""
        try:
            await self._writer.drain()
        except OSError as e:
            self._fail(LinkError(f"write error: {e}"))
            raise self.failed.exception() from None

    async def settle(self) -> None:
        """Waits until everything sent has been consumed by the MCU."""
        while self.in_flight:
            self._credit.clear()
            try:
                await asyncio.wait_for(self._credit.wait(), STALL_TIMEOUT_S)
            except TimeoutError:
                self._fail(LinkError(f"credit stalled ({self.in_flight} B in flight)"))
                raise self.failed.exception() from None

    async def blit(self, x: int, y: int, w: int, h: int, pixels: bytes) -> None:
        if len(pixels) != w * h * 2:
            raise ValueError(f"{w}×{h} needs {w * h * 2} B, got {len(pixels)} B")
        await self.send(p.blit_begin(x, y, w, h))
        chunk = p.MAX_PAYLOAD  # even, so pixels are never split
        for i in range(0, len(pixels), chunk):
            await self.send(p.encode(p.BLIT_DATA, pixels[i:i + chunk]))
        await self.send(p.encode(p.BLIT_END))

    def _fail(self, exc: Exception) -> None:
        if not self.failed.done():
            self.failed.set_exception(exc)

    async def _rx_loop(self) -> None:
        try:
            while data := await self._reader.read(4096):
                for msg_type, payload in self._decoder.feed(data):
                    self._dispatch(msg_type, payload)
            self._fail(LinkError("peer closed the connection"))
        except Exception as e:  # noqa: BLE001 — any error takes the link down
            self._fail(LinkError(f"read error: {e}"))

    def _dispatch(self, msg_type: int, payload: bytes) -> None:
        if msg_type == p.HELLO_ACK:
            if self._ack and not self._ack.done():
                self._ack.set_result(p.HelloAck.parse(payload))
        elif msg_type == p.CREDIT:
            (consumed,) = struct.unpack("<I", payload)
            if consumed != self.consumed:
                self.consumed = consumed
                self._last_progress = time.monotonic()
                self._credit.set()
        elif msg_type == p.BUTTON:
            self.events.put_nowait(p.Button(*payload[:3]))
        elif msg_type == p.LOG:
            self.events.put_nowait(p.Log(payload.decode("utf-8", "replace")))
        else:
            log.warning("unknown frame 0x%02X", msg_type)

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_S)
            if self.in_flight and time.monotonic() - self._last_progress > STALL_TIMEOUT_S:
                self._fail(LinkError(f"credit stalled ({self.in_flight} B in flight)"))
                return
            try:
                await self.send(p.encode(p.PING))
            except LinkError:
                return
