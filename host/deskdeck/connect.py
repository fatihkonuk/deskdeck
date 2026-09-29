"""Connects to the device over TCP and reconnects with exponential backoff when the link drops.

Resolution order: fixed IP from the config, last cached IP, our own mDNS query (mdns.py; on some mesh
networks the system resolver cannot resolve .local at all), and finally the system resolver. The IP
of a successful connection is cached."""
import asyncio
import logging
import socket
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from . import mdns
from .config import DeviceConfig
from .link import Link, LinkError

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 5.0
RESOLVE_TIMEOUT_S = 5.0
BACKOFF_MIN_S, BACKOFF_MAX_S = 1.0, 10.0  # once the device is back the screen stays blank for at most ~10 s
STABLE_SESSION_S = 10.0  # the backoff resets after a session that lasted this long
IP_CACHE = Path.home() / "Library/Caches/deskdeck/device_ip"
BRIDGE_UDP_PORT = 7789  # bridge diagnostics port: "deskdeck?" → status text (bridge/src/main.cpp)
_bg_tasks: set[asyncio.Task] = set()


async def _log_bridge_status(ip: str) -> None:
    """Logs the bridge's uptime, reset reason and Wi-Fi events, so after an outage you can tell
    whether the ESP restarted or dropped off Wi-Fi."""
    loop = asyncio.get_running_loop()
    reply: asyncio.Future[bytes] = loop.create_future()

    class _Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr) -> None:
            if not reply.done():
                reply.set_result(data)

    transport, _ = await loop.create_datagram_endpoint(_Proto, remote_addr=(ip, BRIDGE_UDP_PORT))
    try:
        for _ in range(3):  # UDP may get lost
            transport.sendto(b"deskdeck?")
            try:
                data = await asyncio.wait_for(asyncio.shield(reply), 1.0)
                log.info("bridge: %s", data.decode(errors="replace"))
                return
            except TimeoutError:
                pass
        log.info("no bridge status (older bridge firmware or UDP loss)")
    finally:
        transport.close()


async def _resolve(host: str) -> str:
    if host.endswith(".local"):
        try:
            return await mdns.resolve(host)
        except (OSError, TimeoutError) as e:
            log.info("own mDNS query failed (%s), trying the system resolver", e)
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(loop.getaddrinfo(host, None, family=socket.AF_INET), RESOLVE_TIMEOUT_S)
    return infos[0][4][0]


async def _open(ip: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT_S)
    sock = writer.get_extra_info("socket")
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # don't delay small CREDIT/PING frames
    return reader, writer


async def open_link(cfg: DeviceConfig) -> Link:
    """Connects and opens the session (HELLO + token). In order: fixed IP, cached IP, mDNS."""
    candidates = [cfg.ip] if cfg.ip else []
    if not cfg.ip and IP_CACHE.exists():
        candidates.append(IP_CACHE.read_text().strip())
    errors = []
    for ip in [*candidates, None]:
        try:
            ip = ip or await _resolve(cfg.host)
            reader, writer = await _open(ip, cfg.port)
        except (OSError, TimeoutError) as e:
            errors.append(f"{ip or cfg.host}: {str(e) or type(e).__name__}")
            continue
        link = Link(reader, writer)
        await link.open(cfg.token.encode())  # the bridge closes on a wrong token → LinkError
        if not cfg.ip:
            IP_CACHE.parent.mkdir(parents=True, exist_ok=True)
            IP_CACHE.write_text(ip)
        log.info("connected: %s:%d", ip, cfg.port)
        task = asyncio.create_task(_log_bridge_status(ip))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
        return link
    raise LinkError("could not connect: " + "; ".join(errors))


async def run_forever(cfg: DeviceConfig, session: Callable[[Link], Awaitable[None]],
                      on_event: Callable[[object], None] = print) -> None:
    """Connect, then run the session(link) task (redraws the whole screen, then sends updates)
    alongside event dispatch. If the link drops or the session fails, wait and try again.
    on_event must return quickly (spawn long work as its own task) so buttons keep arriving during blits."""
    backoff = BACKOFF_MIN_S
    while True:
        connected_at = None
        link = None
        sess = None
        try:
            link = await open_link(cfg)
            connected_at = time.monotonic()
            sess = asyncio.create_task(session(link))
            while True:
                get = asyncio.ensure_future(link.events.get())
                done, _ = await asyncio.wait({get, link.failed, sess}, return_when=asyncio.FIRST_COMPLETED)
                if get not in done:
                    get.cancel()
                if link.failed in done:
                    raise link.failed.exception()
                if sess in done:
                    sess.result()  # re-raise if it failed
                    raise LinkError("session task ended")
                on_event(get.result())
        except LinkError as e:
            log.warning("link: %s", e)
        finally:
            if sess and not sess.done():
                sess.cancel()
            elif sess and not sess.cancelled():
                sess.exception()  # the link error was logged above; avoid a "never retrieved" warning
            if link:
                await link.close()
        # only an established connection counts; a failed attempt can also take ~10 s
        if connected_at is not None and time.monotonic() - connected_at > STABLE_SESSION_S:
            backoff = BACKOFF_MIN_S
        log.info("retrying in %.0f s", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, BACKOFF_MAX_S)
