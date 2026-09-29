import asyncio
import base64
import gc
import json
import sys

import pytest

from deskdeck import nowplaying
from deskdeck.nowplaying import Watcher


def line(payload: dict, diff: bool = False) -> bytes:
    return json.dumps({"type": "data", "diff": diff, "payload": payload}).encode()


def art(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_diff_merges_into_state():
    w = Watcher()
    w._apply(line({"title": "Song", "artist": "Artist", "playing": True}))
    w._apply(line({"playing": False}, diff=True))
    assert (w.state.title, w.state.artist, w.state.playing) == ("Song", "Artist", False)
    w._apply(line({"artist": None}, diff=True))  # null removes the field
    assert w.state.artist == ""


def test_artwork_is_decoded_only_when_it_changes(monkeypatch):
    decoded: list[str] = []
    real = nowplaying.base64.b64decode

    def b64decode(s):
        decoded.append(s)
        return real(s)

    monkeypatch.setattr(nowplaying.base64, "b64decode", b64decode)
    w = Watcher()
    w._apply(line({"title": "Song", "artworkData": art(b"cover-1")}))
    first = w.state.artwork_key
    for playing in (True, False, True):
        w._apply(line({"playing": playing}, diff=True))
    assert len(decoded) == 1
    assert (w.state.artwork, w.state.artwork_key) == (b"cover-1", first)

    # a full (non-diff) message repeating the same artwork does not decode again either
    w._apply(line({"title": "Song", "artworkData": art(b"cover-1")}))
    assert len(decoded) == 1

    w._apply(line({"artworkData": art(b"cover-2")}, diff=True))
    assert len(decoded) == 2
    assert w.state.artwork == b"cover-2"
    assert w.state.artwork_key not in ("", first)

    w._apply(line({"artworkData": None}, diff=True))
    assert (w.state.artwork, w.state.artwork_key) == (None, "")


def test_bad_artwork_does_not_freeze_updates(caplog):
    w = Watcher()
    w._apply(line({"title": "Song", "playing": True, "artworkData": "not base64!"}))
    assert (w.state.title, w.state.artwork) == ("Song", None)
    w._apply(line({"playing": False}, diff=True))
    w._apply(line({"title": "Next"}, diff=True))
    assert (w.state.title, w.state.playing) == ("Next", False)
    assert caplog.text.count("could not decode artwork") == 1


def test_non_object_lines_are_rejected_without_touching_state():
    w = Watcher()
    w._apply(line({"title": "Song"}))
    for bad in (b"[1, 2]", b'"text"', json.dumps({"type": "data", "diff": True, "payload": [1]}).encode()):
        with pytest.raises(TypeError):
            w._apply(bad)
    assert w.state.title == "Song"


def test_field_of_wrong_type_does_not_freeze_updates():
    w = Watcher()
    w._apply(line({"title": "Song", "durationMicros": 200_000_000, "playing": True}))
    w._apply(line({"durationMicros": "abc", "playbackRate": [1], "title": 5}, diff=True))
    assert (w.state.title, w.state.duration_s, w.state.rate) == ("", 0.0, 0.0)
    w._apply(line({"title": "Next"}, diff=True))
    assert w.state.title == "Next"


def run_watcher(monkeypatch, command: list[str], seconds: float = 0.3) -> Watcher:
    monkeypatch.setattr(nowplaying, "COMMAND", command)
    monkeypatch.setattr(nowplaying, "RESTART_DELAY_S", 0.01)
    w = Watcher()

    async def main():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(seconds)
        assert not task.done(), task.exception()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        gc.collect()  # release subprocess transports while the loop is still open
        await asyncio.sleep(0.05)

    asyncio.run(main())
    return w


def test_watcher_survives_a_line_over_the_limit(monkeypatch, caplog):
    """The reader pauses the pipe once a line exceeds the limit. The child stays alive after writing, so
    the pipe is still open when the watcher kills it: cleaning up must not wait for a close that the
    paused reader never sees."""
    monkeypatch.setattr(nowplaying, "LINE_LIMIT", 100)
    child = "import sys, time; sys.stdout.write('x' * 100000); sys.stdout.flush(); time.sleep(30)"
    run_watcher(monkeypatch, [sys.executable, "-c", child])
    assert "media-control watcher failed" in caplog.text


def test_watcher_survives_a_spawn_failure(monkeypatch, caplog, tmp_path):
    run_watcher(monkeypatch, [str(tmp_path)])  # a directory: PermissionError, not FileNotFoundError
    assert "media-control watcher failed" in caplog.text


def test_watcher_reads_media_control_output(monkeypatch):
    out = json.dumps({"type": "data", "diff": False, "payload": {"title": "From the stream"}})
    w = run_watcher(monkeypatch, [sys.executable, "-c", f"print({out!r})"])
    assert w.state.title == "From the stream"


def test_out_of_range_numbers_read_as_zero():
    """Negative, NaN or infinite values must not reach PROGRESS, whose packing would fail on every
    redraw and send the session into a reconnect loop."""
    w = Watcher()
    w._apply(b'{"type": "data", "diff": false, "payload": {"title": "Song", "playing": true, '
             b'"durationMicros": -1000000, "elapsedTimeMicros": NaN, "playbackRate": Infinity}}')
    assert (w.state.duration_s, w.state.elapsed_s, w.state.rate) == (0.0, 0.0, 0.0)
    w._apply(line({"playbackRate": 1e308, "timestampEpochMicros": 1}, diff=True))
    assert w.state.elapsed_now() == 0.0  # overflows to infinity, read as 0
