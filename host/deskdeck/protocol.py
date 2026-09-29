"""Protocol v1 framing. Single source of truth: docs/protocol.md (must match firmware/include/proto.h)."""
import binascii
import struct
from dataclasses import dataclass

PROTO_VERSION = 1
SYNC = b"\xA5\x5A"
HEADER_LEN = 5
CRC_LEN = 2
MAX_PAYLOAD = 512

# Host → MCU
HELLO = 0x01
BLIT_BEGIN = 0x02
BLIT_DATA = 0x03
BLIT_END = 0x04
PROGRESS = 0x05
PING = 0x06
FILL = 0x07
# MCU → Host
HELLO_ACK = 0x81
CREDIT = 0x82
BUTTON = 0x83
LOG = 0x84

PRESS, RELEASE, LONG, CANCEL, SWIPE_LEFT, SWIPE_RIGHT = range(6)
BUTTON_EVENTS = {PRESS: "press", RELEASE: "release", LONG: "long", CANCEL: "cancel",
                 SWIPE_LEFT: "swipe left", SWIPE_RIGHT: "swipe right"}


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE."""
    return binascii.crc_hqx(data, 0xFFFF)


def encode(msg_type: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)} B > {MAX_PAYLOAD}")
    body = struct.pack("<BH", msg_type, len(payload)) + payload
    return SYNC + body + struct.pack("<H", crc16(body))


class Decoder:
    """Extracts valid frames from a byte stream; on corrupt data skips one byte and hunts for sync."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        buf = self._buf
        buf += data
        frames = []
        while True:
            i = buf.find(SYNC)
            if i < 0:
                del buf[:-1 if buf.endswith(SYNC[:1]) else len(buf)]
                break
            del buf[:i]
            if len(buf) < HEADER_LEN:
                break
            msg_type, length = buf[2], buf[3] | buf[4] << 8
            if length > MAX_PAYLOAD:
                del buf[:1]
                continue
            total = HEADER_LEN + length + CRC_LEN
            if len(buf) < total:
                break
            (crc,) = struct.unpack_from("<H", buf, HEADER_LEN + length)
            if crc != crc16(bytes(buf[2:HEADER_LEN + length])):
                self.crc_errors += 1
                del buf[:1]
                continue
            frames.append((msg_type, bytes(buf[HEADER_LEN:HEADER_LEN + length])))
            del buf[:total]
        return frames


@dataclass(frozen=True)
class HelloAck:
    proto: int
    firmware: int
    width: int
    height: int
    window: int
    max_payload: int

    @classmethod
    def parse(cls, p: bytes) -> "HelloAck":
        return cls(*struct.unpack("<BHHHHH", p[:11]))


@dataclass(frozen=True)
class Button:
    page: int
    index: int
    event: int

    def __str__(self) -> str:
        return f"BUTTON page={self.page} index={self.index} {BUTTON_EVENTS.get(self.event, self.event)}"


@dataclass(frozen=True)
class Log:
    text: str

    def __str__(self) -> str:
        return f"LOG {self.text}"


def hello(token: bytes = b"") -> bytes:
    return encode(HELLO, bytes([PROTO_VERSION]) + token)


def blit_begin(x: int, y: int, w: int, h: int) -> bytes:
    return encode(BLIT_BEGIN, struct.pack("<4H", x, y, w, h))


def fill(x: int, y: int, w: int, h: int, color: int) -> bytes:
    return encode(FILL, struct.pack("<5H", x, y, w, h, color))


def progress(elapsed_ms: int, duration_ms: int, playing: bool) -> bytes:
    """Values are clamped to the u32 range, so out-of-range media data cannot make packing fail."""
    def u32(v: int) -> int:
        return min(max(v, 0), 0xFFFFFFFF)

    return encode(PROGRESS, struct.pack("<IIB", u32(elapsed_ms), u32(duration_ms), playing))
