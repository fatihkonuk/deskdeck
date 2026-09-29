"""Tracks what is playing with `media-control stream` (https://github.com/ungive/media-control).

The output is JSON lines: {"type": "data", "diff": bool, "payload": {...}}. diff=false is the full
state (empty payload = nothing playing); diff=true carries only the changed fields, which are merged
into the current state. If the tool exits it is restarted after a short delay."""
import asyncio
import base64
import hashlib
import json
import logging
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
        return max(0.0, min(e, self.duration_s)) if self.duration_s else max(0.0, e)


def _to_state(p: dict, artwork: bytes | None, artwork_key: str) -> NowPlaying:
    return NowPlaying(
        title=p.get("title") or "",
        artist=p.get("artist") or "",
        album=p.get("album") or "",
        duration_s=(p.get("durationMicros") or 0) / 1e6,
        elapsed_s=(p.get("elapsedTimeMicros") or 0) / 1e6,
        timestamp=(p.get("timestampEpochMicros") or 0) / 1e6 or time.time(),
        rate=float(p.get("playbackRate") or 0.0),
        playing=bool(p.get("playing")),
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
        if msg.get("type") != "data":
            return
        payload = msg.get("payload") or {}
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
            data = base64.b64decode(src) if src else None
            self._artwork = (data, hashlib.sha1(data).hexdigest() if data else "")
            self._artwork_src = src
        return self._artwork

    async def run(self) -> None:
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *COMMAND, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=LINE_LIMIT)
            except FileNotFoundError:
                log.error("media-control not found: brew install ungive/media-control/media-control")
                await asyncio.sleep(30)
                continue
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
                await proc.wait()
            log.warning("media-control exited (code %s), restarting in %.0f s", proc.returncode, RESTART_DELAY_S)
            await asyncio.sleep(RESTART_DELAY_S)
