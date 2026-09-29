"""One-shot mDNS A query ("legacy unicast", RFC 6762 §6.7).

The query is multicast from a port other than 5353, so the responder answers with a unicast reply
straight to that port. The macOS system resolver expects a multicast reply, and some mesh networks
(e.g. TP-Link Deco) do not forward multicast from wireless clients, so .local names never resolve
there. This path works in that case too."""
import asyncio
import socket
import struct

MDNS_ADDR = ("224.0.0.251", 5353)


def _query(name: str) -> bytes:
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.rstrip(".").split(".")) + b"\0"
    return struct.pack(">6H", 0, 0, 1, 0, 0, 0) + qname + struct.pack(">HH", 1, 1)  # A, IN


def _skip_name(msg: bytes, i: int) -> int:
    while True:
        n = msg[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:  # compression pointer
            return i + 2
        i += 1 + n


def _parse_a(msg: bytes) -> str | None:
    _, flags, qd, an, ns, ar = struct.unpack_from(">6H", msg)
    if not flags & 0x8000:
        return None
    i = 12
    for _ in range(qd):
        i = _skip_name(msg, i) + 4
    for _ in range(an + ns + ar):
        i = _skip_name(msg, i)
        rtype, _, _, rdlen = struct.unpack_from(">HHIH", msg, i)
        i += 10
        if rtype == 1 and rdlen == 4:
            return socket.inet_ntoa(msg[i:i + 4])
        i += rdlen
    return None


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, fut: asyncio.Future[str]) -> None:
        self.fut = fut

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            ip = _parse_a(data)
        except (struct.error, IndexError):
            return
        if ip and not self.fut.done():
            self.fut.set_result(ip)


async def resolve(name: str, timeout: float = 2.0, attempts: int = 3) -> str:
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.bind(("", 0))
    transport, _ = await loop.create_datagram_endpoint(lambda: _Proto(fut), sock=sock)
    try:
        for _ in range(attempts):
            transport.sendto(_query(name), MDNS_ADDR)
            try:
                return await asyncio.wait_for(asyncio.shield(fut), timeout / attempts)
            except TimeoutError:
                continue
        raise TimeoutError(f"mDNS: no answer for {name}")
    finally:
        transport.close()
