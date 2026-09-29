import base64
import json

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
