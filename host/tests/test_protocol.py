import struct

from deskdeck import protocol as p


def test_crc16_ccitt_check_value():
    assert p.crc16(b"123456789") == 0x29B1


def test_decoder_skips_junk_and_splits_frames():
    frames = p.Decoder().feed(b"junk" + p.encode(p.PING) + p.progress(1, 2, True))
    assert [t for t, _ in frames] == [p.PING, p.PROGRESS]


def test_decoder_handles_frames_split_across_reads():
    data = p.encode(p.LOG, b"hello")
    dec = p.Decoder()
    assert dec.feed(data[:4]) == []
    assert dec.feed(data[4:]) == [(p.LOG, b"hello")]


def test_decoder_drops_frame_with_bad_crc():
    bad = bytearray(p.encode(p.LOG, b"hello"))
    bad[-1] ^= 0xFF
    assert p.Decoder().feed(bytes(bad) + p.encode(p.PING)) == [(p.PING, b"")]


def test_progress_clamps_to_u32():
    for elapsed, duration in ((-5, -1000), (2**40, 2**33)):
        ((t, payload),) = p.Decoder().feed(p.progress(elapsed, duration, True))
        assert t == p.PROGRESS
        assert payload[:8] == struct.pack("<II", max(0, min(elapsed, 2**32 - 1)), max(0, min(duration, 2**32 - 1)))
