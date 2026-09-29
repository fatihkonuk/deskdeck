"""Link test: HELLO, PNG blit, text, button icons, PROGRESS; then prints BUTTON/LOG events.

  uv run deskdeck-wiretest --image cover.png             # serial port (wired, see docs/hardware.md)
  uv run deskdeck-wiretest --full screen.png             # full-screen blit (flow-control stress test)
  uv run deskdeck-wiretest --wifi --image cover.png      # Wi-Fi via config.toml; reconnects if the link drops
"""
import argparse
import asyncio
import glob
import logging
import sys
import time

import serial_asyncio
from PIL import Image, ImageDraw, ImageOps

from . import config, layout
from . import protocol as p
from .connect import run_forever
from .image import font, rgb565, to_rgb565
from .link import Link, LinkError

BAUD = 921600
BG = (0, 0, 0)


def default_port() -> str:
    ports = sorted(glob.glob("/dev/cu.usbserial-*")) or sorted(glob.glob("/dev/cu.SLAB_USBtoUART*"))
    if not ports:
        sys.exit("No serial port found (/dev/cu.usbserial-*). Pass one with --port.")
    return ports[0]


def text_image(w: int, h: int, lines: list[tuple[str, int, tuple[int, int, int]]]) -> Image.Image:
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    y = 0
    for text, size, color in lines:
        f = font(size)
        d.text((0, y), text, font=f, fill=color)
        y += int(size * 1.3)
    return img


def button_icon(index: int) -> Image.Image:
    _, _, w, h = layout.icon_rect(index)
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    d.text((w / 2, h / 2), str(index + 1), font=font(36), fill=(200, 200, 210), anchor="mm")
    return img


async def blit_image(link: Link, rect: tuple[int, int, int, int], img: Image.Image) -> None:
    x, y, w, h = rect
    await link.blit(x, y, w, h, to_rgb565(img.resize((w, h)) if img.size != (w, h) else img))


def print_ack(ack: p.HelloAck) -> None:
    print(f"HELLO_ACK: fw=0x{ack.firmware:04X} screen={ack.width}×{ack.height} "
          f"window={ack.window} B max payload={ack.max_payload} B", flush=True)


async def run_wifi(args: argparse.Namespace) -> None:
    cfg = config.load()
    print(f"Wi-Fi: {cfg.ip or cfg.host}:{cfg.port}", flush=True)

    drawn = False

    async def session(link: Link) -> None:
        nonlocal drawn
        print_ack(link.ack)
        await draw_test_screen(link, args)
        drawn = True
        await asyncio.Event().wait()  # until the link drops

    def on_event(ev: object) -> None:
        print(f"{time.strftime('%H:%M:%S')} {ev}", flush=True)

    # a setup error (e.g. a missing --image) should fail the test, not be retried until --seconds runs out
    try:
        await asyncio.wait_for(run_forever(cfg, session, on_event, retry_unexpected=False), args.seconds)
    except TimeoutError:
        if not drawn:  # run_forever kept retrying the connection: that is a failed test, not a pass
            raise LinkError(f"the test screen was not drawn within {args.seconds:.0f} s (see the log above)") from None
        raise


async def run(args: argparse.Namespace) -> None:
    port = args.port or default_port()
    print(f"Port: {port} @ {BAUD}")
    reader, writer = await serial_asyncio.open_serial_connection(url=port, baudrate=BAUD)
    link = Link(reader, writer)
    print_ack(await link.open())
    await draw_test_screen(link, args)
    print(f"Listening for events for {args.seconds} s (Ctrl-C to quit)…")

    deadline = time.monotonic() + args.seconds
    while (left := deadline - time.monotonic()) > 0:
        get = asyncio.ensure_future(link.events.get())
        done, _ = await asyncio.wait({get, link.failed}, timeout=left, return_when=asyncio.FIRST_COMPLETED)
        if link.failed in done:
            get.cancel()
            raise link.failed.exception()
        if get in done:
            print(f"{time.strftime('%H:%M:%S')} {get.result()}", flush=True)
        else:
            get.cancel()
    await link.close()


async def draw_test_screen(link: Link, args: argparse.Namespace) -> None:
    if args.full:
        img = ImageOps.fit(Image.open(args.full), (layout.WIDTH, layout.HEIGHT))
        t0 = time.monotonic()
        await blit_image(link, (0, 0, layout.WIDTH, layout.HEIGHT), img)
        await link.settle()
        dt = time.monotonic() - t0
        n = layout.WIDTH * layout.HEIGHT * 2
        print(f"Full-screen blit: {n} B, {dt:.2f} s, {n / dt / 1024:.1f} KB/s")
        await asyncio.sleep(2)
        await link.hello()  # clears the screen and redraws the button frames

    cover = Image.open(args.image) if args.image else demo_cover()
    t0 = time.monotonic()
    await blit_image(link, layout.COVER, ImageOps.fit(cover, layout.COVER[2:]))
    await link.settle()
    dt = time.monotonic() - t0
    print(f"Cover 128×128: {128 * 128 * 2} B, {dt:.2f} s")

    _, _, tw, th = layout.TEXT
    await blit_image(link, layout.TEXT, text_image(tw, th, [
        ("Now playing: test pattern", 24, (255, 255, 255)),
        ("Unicode: ğüşıöç ĞÜŞİÖÇ ñ é ß", 18, (180, 180, 190)),
        ("Album: Ωμέγα · Кириллица", 14, (140, 140, 150)),
    ]))
    _, _, tw, th = layout.TIME_TEXT
    await blit_image(link, layout.TIME_TEXT, text_image(tw, th, [("0:30 / 3:20", 12, (160, 160, 170))]))
    for i in range(8):
        await blit_image(link, layout.icon_rect(i), button_icon(i))
    await link.send(p.progress(30_000, 200_000, True))
    await link.settle()
    print("Screen drawn.", flush=True)


def demo_cover() -> Image.Image:
    """Used when no PNG is given: a 128×128 gradient test cover with a circle and a line."""
    img = Image.new("RGB", (128, 128))
    px = img.load()
    for y in range(128):
        for x in range(128):
            px[x, y] = (x * 2, y * 2, 255 - x - y // 2)
    d = ImageDraw.Draw(img)
    d.ellipse((24, 24, 104, 104), outline=(255, 255, 255), width=4)
    d.line((0, 0, 127, 127), fill=(255, 255, 0), width=2)
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wifi", action="store_true", help="connect over TCP to the device in config.toml instead of a serial port")
    ap.add_argument("--port")
    ap.add_argument("--image", help="PNG to send as the cover")
    ap.add_argument("--full", help="image to blit full screen first")
    ap.add_argument("--seconds", type=float, default=30)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(run_wifi(args) if args.wifi else run(args))
    except TimeoutError:
        pass  # --seconds elapsed
    except LinkError as e:
        sys.exit(f"Link error: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
