"""deskdeck service: mirrors what is playing onto the screen and maps buttons to Mac actions.

  uv run deskdeck

On connect the whole screen is drawn; after that only the parts that change are sent: text lines,
cover art, the ⏯ icon, PROGRESS (when position/state changes; the MCU advances the bar in between)
and the time label once a second. Button events run in their own tasks without blocking.

Pages come from [[pages]] in config.toml and are reloaded when the file changes. The host owns the
current page: the MCU reports swipes and the host redraws the grid with the new page. Actions run on
release (a press that turns into a swipe does nothing); volume buttons repeat while held. The repeat
also stops when the session ends and after VOLUME_REPEAT_MAX_S, so a RELEASE that never arrives (link
drop, MCU reset, corrupted frame) cannot leave the volume stepping on its own."""
import asyncio
import logging
import time

from pathlib import Path

from . import actions, config, layout, render
from . import protocol as p
from .connect import run_forever
from .image import to_rgb565
from .link import Link
from .nowplaying import NowPlaying, Watcher

log = logging.getLogger(__name__)

VOLUME_REPEAT_S = 0.15
VOLUME_REPEAT_MAX_S = 10.0  # a full 0-100 sweep takes ~4 s including osascript start-up
TICK_S = 0.25


async def blit(link: Link, rect: tuple[int, int, int, int], img) -> None:
    x, y, w, h = rect
    await link.blit(x, y, w, h, to_rgb565(img))


class App:
    def __init__(self, config_path: Path = config.DEFAULT_PATH) -> None:
        self.watcher = Watcher()
        self._tasks: set[asyncio.Task] = set()
        self._repeat: asyncio.Task | None = None
        self._config_path = config_path
        self._config_mtime = config_path.stat().st_mtime
        self.pages = config.load_pages(config_path)
        self.page = 0  # the page to show; changes as soon as a swipe arrives
        # The page whose buttons are on screen, None while the grid is being redrawn. Taps act on this,
        # not on self.page: right after a swipe the old icons are still showing.
        self._shown: config.Page | None = None

    def _reload_pages(self) -> None:
        """Reloads the pages if config.toml changed; keeps the old ones if the new file is invalid."""
        try:
            mtime = self._config_path.stat().st_mtime
        except OSError:
            return
        if mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            self.pages = config.load_pages(self._config_path)
        except (ValueError, OSError) as e:  # OSError: e.g. the file is briefly missing during a save
            log.error("could not load pages from config.toml, keeping the previous ones: %s", e)
            return
        self.page = min(self.page, len(self.pages) - 1)
        log.info("pages reloaded (%d pages)", len(self.pages))

    # ---- screen ----

    async def session(self, link: Link) -> None:
        drawn: dict[str, object] = {}
        self.watcher.changed.set()  # draw everything on the first pass
        try:
            while True:
                self._reload_pages()
                np = self.watcher.state
                now = time.time()
                await self._draw(link, np, now, drawn)
                try:
                    await asyncio.wait_for(self.watcher.changed.wait(), TICK_S)
                except TimeoutError:
                    pass
                self.watcher.changed.clear()
        finally:
            # A press held while the link dropped never gets its RELEASE; the MCU also forgets the
            # touch when it goes offline or receives a new HELLO.
            self._stop_repeat()
            self._shown = None

    async def _draw(self, link: Link, np: NowPlaying, now: float, drawn: dict) -> None:
        text_key = (np.title, np.artist, np.album)
        song_changed = drawn.get("text") != text_key
        if song_changed:
            for line in render.text_lines(np):
                await self._draw_line(link, line, drawn)
            drawn["text"] = text_key
        if drawn.get("cover") != (np.artwork_key, np.empty):
            # resampling large artwork takes ~20 ms (1200×1200): keep it off the event loop
            await blit(link, layout.COVER, await asyncio.to_thread(render.cover, np))
            drawn["cover"] = (np.artwork_key, np.empty)
        if song_changed and "grid" in drawn:
            log.info("track: %s — %s | screen updated after %.2f s", np.title, np.artist,
                     time.monotonic() - self.watcher.changed_at)
        await self._draw_grid(link, np, drawn)
        progress_key = (np.playing, np.duration_s, np.elapsed_s, np.timestamp)
        if drawn.get("progress") != progress_key:
            # Position at send time, not `now`: after a song change the blits above wait on credit for
            # up to ~1 s, and the MCU advances the bar from the moment PROGRESS arrives.
            await link.send(p.progress(int(np.elapsed_now() * 1000), int(np.duration_s * 1000), np.playing))
            drawn["progress"] = progress_key
        label = render.time_label(np, now)
        if drawn.get("time") != label:
            await blit(link, layout.TIME_TEXT_BLIT, render.time_image(label))
            drawn["time"] = label

    async def _draw_grid(self, link: Link, np: NowPlaying, drawn: dict) -> None:
        """If the page or its definitions changed, clears the icon areas and redraws the cells.
        ⏯ cells are updated separately according to the playback state."""
        page = self.pages[self.page]
        if drawn.get("grid") != (self.page, page):
            drawn["grid"] = (self.page, page)
            drawn.pop("play_icon", None)
            self._shown = None  # old and new icons are mixed until the loop below is done
            for i in range(layout.BUTTON_COUNT):
                await link.send(p.fill(*layout.icon_rect(i), 0))
            cells = await asyncio.to_thread(lambda: [render.cell(s) if s and not _is_playpause(s) else ()
                                                     for s in page])
            for i, parts in enumerate(cells):
                x, y, _, _ = layout.icon_rect(i)
                for dx, dy, img in parts:
                    await blit(link, (x + dx, y + dy, img.width, img.height), img)
            await blit(link, layout.PAGE_DOTS, render.page_dots(len(self.pages), self.page))
            self._shown = page
        play_icon = "pause" if np.playing else "play"
        if drawn.get("play_icon") != play_icon:
            for i, spec in enumerate(page):
                if spec and _is_playpause(spec):
                    await blit(link, layout.icon_rect(i), render.icon(play_icon))
            drawn["play_icon"] = play_icon

    async def _draw_line(self, link: Link, line: render.TextLine, drawn: dict) -> None:
        """Sends only a line that changed: the text is blitted at its own width, the rest is FILLed black."""
        rect, text, _, color = line
        key = ("line", rect)
        if drawn.get(key) == (text, color):
            return
        x, y, w, h = rect
        tw = 0
        if text:
            img = render.text_image(line)
            tw = img.width
            await blit(link, (x, y, tw, h), img)
        if tw < w:
            await link.send(p.fill(x + tw, y, w - tw, h, 0))
        drawn[key] = (text, color)

    # ---- buttons ----

    def on_event(self, ev: object) -> None:
        if isinstance(ev, p.Log):
            log.info("MCU: %s", ev.text)
            return
        if not isinstance(ev, p.Button):
            return
        if ev.event in (p.SWIPE_LEFT, p.SWIPE_RIGHT):
            self._stop_repeat()
            step = 1 if ev.event == p.SWIPE_LEFT else -1
            self.page = (self.page + step) % len(self.pages)
            log.info("page %d/%d", self.page + 1, len(self.pages))
            self.watcher.changed.set()  # wake the draw loop right away
            return
        # _repeat marks the current touch as a long press until the touch ends. It is handled before the
        # button lookup: a config reload may have emptied the cell while the finger was down.
        if ev.event == p.PRESS or ev.event == p.CANCEL:
            # PRESS starts a new touch: anything left from the previous one (a RELEASE that never
            # arrived, a repeat stopped by its time limit) is over.
            self._stop_repeat()
            return
        if ev.event == p.RELEASE and self._repeat:  # the long press already repeated the action
            self._stop_repeat()
            return
        if self._shown is None:
            if ev.event == p.RELEASE:
                log.info("tap ignored: the page is being redrawn")
            return
        spec = self._shown[ev.index] if ev.index < layout.BUTTON_COUNT else None
        if spec is None:
            return
        if ev.event == p.LONG and spec.action == "media" and spec.arg.startswith("vol_"):
            self._stop_repeat()
            self._repeat = self._spawn(self._repeat_action(spec))
        elif ev.event == p.RELEASE:
            log.info("button: %s", spec.name)
            self._spawn(actions.perform(spec))

    def _stop_repeat(self) -> None:
        if self._repeat:
            self._repeat.cancel()
            self._repeat = None

    async def _repeat_action(self, spec: config.ButtonSpec) -> None:
        deadline = time.monotonic() + VOLUME_REPEAT_MAX_S
        while time.monotonic() < deadline:
            await actions.perform(spec)
            await asyncio.sleep(VOLUME_REPEAT_S)
        log.warning("%s: no release after %.0f s, repeat stopped", spec.name, VOLUME_REPEAT_MAX_S)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():  # e.g. media-control is not installed
            log.error("action failed", exc_info=task.exception())


def _is_playpause(spec: config.ButtonSpec) -> bool:
    return spec.action == "media" and spec.arg == "playpause" and not spec.icon


async def amain() -> None:
    cfg = config.load()
    app = App()
    watcher = asyncio.create_task(app.watcher.run())
    try:
        await run_forever(cfg, app.session, app.on_event)
    finally:
        watcher.cancel()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
