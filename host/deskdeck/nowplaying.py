"""Tracks what is playing with `media-control stream` (https://github.com/ungive/media-control).

The output is JSON lines: {"type": "data", "diff": bool, "payload": {...}}. diff=false is the full
state (empty payload = nothing playing); diff=true carries only the changed fields, which are merged
into the current state. If the tool exits it is restarted after a short delay."""
import asyncio
import base64
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

COMMAND = ["media-control", "stream", "--micros"]
RESTART_DELAY_S = 2.0
LINE_LIMIT = 64 * 1024 * 1024  # base64 artwork can be a few hundred KB


@dataclass(frozen=True)
class NowPlaying:
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_s: float = 0.0
    elapsed_s: float = 0.0  # position at `timestamp`
    timestamp: float = 0.0  # epoch seconds
    rate: float = 0.0
    playing: bool = False
    artwork: bytes | None = field(default=None, repr=False)
    artwork_key: str = ""  # for detecting artwork changes

    @property
    def empty(self) -> bool:
        return not self.title

    def elapsed_now(self, now: float | None = None) -> float:
        e = self.elapsed_s
        if self.playing:
            e += ((now or time.time()) - self.timestamp) * (self.rate or 1.0)
        e = max(0.0, min(e, self.duration_s)) if self.duration_s else max(0.0, e)
        return e if math.isfinite(e) else 0.0  # e.g. an absurd playbackRate


def _text(v: object) -> str:
    return v if isinstance(v, str) else ""


def _number(v: object) -> float:
    """A field of the wrong type, or a negative or non-finite value (json.loads accepts NaN and
    Infinity), reads as 0 instead of raising: it stays in the merged payload, so an exception would repeat
    on every later update. All fields read with this are durations, positions, times or rates."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0.0
    v = float(v)
    return v if math.isfinite(v) and v >= 0 else 0.0


def _to_state(p: dict, artwork: bytes | None, artwork_key: str) -> NowPlaying:
    return NowPlaying(
        title=_text(p.get("title")),
        artist=_text(p.get("artist")),
        album=_text(p.get("album")),
        duration_s=_number(p.get("durationMicros")) / 1e6,
        elapsed_s=_number(p.get("elapsedTimeMicros")) / 1e6,
        timestamp=_number(p.get("timestampEpochMicros")) / 1e6 or time.time(),
        rate=_number(p.get("playbackRate")),
        playing=p.get("playing") is True,
        artwork=artwork,
        artwork_key=artwork_key,
    )


class Watcher:
    """Keeps the current state in `state` and sets the `changed` event on every change."""

    def __init__(self) -> None:
        self.state = NowPlaying()
        self.changed = asyncio.Event()
        self.changed_at = time.monotonic()  # arrival time of the last update (latency measurement)
        self._payload: dict = {}
        self._artwork_src: str | None = None  # the base64 text that _artwork was decoded from
        self._artwork: tuple[bytes | None, str] = (None, "")

    def _apply(self, line: bytes) -> None:
        msg = json.loads(line)
        if not isinstance(msg, dict):
            raise TypeError(f"expected a JSON object, got {type(msg).__name__}")
        if msg.get("type") != "data":
            return
        payload = msg.get("payload") or {}
        if not isinstance(payload, dict):
            raise TypeError(f"expected an object as payload, got {type(payload).__name__}")
        if msg.get("diff"):
            for k, v in payload.items():
                if v is None:
                    self._payload.pop(k, None)
                else:
                    self._payload[k] = v
        else:
            self._payload = dict(payload)
        self.state = _to_state(self._payload, *self._decode_artwork())
        self.changed_at = time.monotonic()
        self.changed.set()

    def _decode_artwork(self) -> tuple[bytes | None, str]:
        """Decodes and hashes the artwork only when it changed. The base64 text is a few hundred KB,
        and most updates (play/pause, position) are diffs that leave it untouched: the merged payload
        then still holds the same string object, so the comparison is a pointer check."""
        src = self._payload.get("artworkData")
        if src != self._artwork_src:
            self._artwork_src = src
            try:
                data = base64.b64decode(src) if src else None
            except (ValueError, TypeError) as e:  # binascii.Error, or a str with non-ASCII characters
                # Raising here would drop this update and every later diff too, since the bad text
                # stays in the merged payload: show no artwork instead.
                log.warning("could not decode artwork: %s", e)
                data = None
            self._artwork = (data, hashlib.sha1(data).hexdigest() if data else "")
        return self._artwork

    async def run(self) -> None:
        """Runs media-control for as long as the service runs. Nothing escapes this loop but
        cancellation: nobody awaits the task, so an escaping error would silently freeze the screen
        on the last track."""
        while True:
            try:
                await self._run_once()
            except FileNotFoundError:
                log.error("media-control not found: brew install ungive/media-control/media-control")
                await asyncio.sleep(30)
                continue
            except Exception:  # e.g. a line over LINE_LIMIT, or a spawn failure
                log.exception("media-control watcher failed")
            await asyncio.sleep(RESTART_DELAY_S)

    async def _run_once(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            *COMMAND, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=LINE_LIMIT)
        try:
            assert proc.stdout
            while line := await proc.stdout.readline():
                try:
                    self._apply(line)
                except (ValueError, TypeError) as e:
                    log.warning("could not parse media-control line: %s", e)
        finally:
            if proc.returncode is None:
                proc.kill()
            # Not wait(): it only returns once every pipe is closed, and after a line over LINE_LIMIT
            # the reader has paused the stdout pipe, so it would never close and the watcher would hang.
            # communicate() reads (and discards) what is left, then waits.
            await proc.communicate()
        log.warning("media-control exited (code %s), restarting in %.0f s", proc.returncode, RESTART_DELAY_S)
