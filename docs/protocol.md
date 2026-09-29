# Protocol v1

Spoken between the Mac host (`host/deskdeck/protocol.py`) and the STM32 (`firmware/include/proto.h`).
The byte stream is identical whether it runs over a serial port (wired testing) or over TCP through
the ESP8266, which is a transparent bridge.

## Frame

```
A5 5A | type (1) | length (2, LE) | payload (0..512) | CRC (2, LE)
```

- **CRC**: CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no final XOR), computed over
  type, length and payload. `"123456789"` → `0x29B1`. Python: `binascii.crc_hqx(data, 0xFFFF)`.
- All multi-byte fields are little-endian. **Exception**: pixels inside BLIT_DATA are RGB565
  **big-endian** (high byte first) because they are streamed straight to the display.
- Maximum payload is 512 B, maximum frame 519 B.
- The receiver finds sync by searching for `A5 5A`. If the header does not fit (unknown type, or a
  length that does not match the type, see the table below) or the CRC fails, it skips one byte and
  keeps searching. Checking the header before waiting for the payload keeps an `A5 5A` that happens to
  appear inside pixel data from stalling the parser. Unknown types are therefore dropped like noise;
  a host with a different protocol version is caught at HELLO instead.
- When a client disconnects or is replaced, the bridge writes 519 zero bytes (one maximum-size frame)
  to the UART. A frame that was cut off mid-way is then completed with zeros, fails its CRC, and the
  parser is back in sync before the next client's HELLO arrives, instead of swallowing it.

## Messages

| Type | Direction | Name | Payload |
|---|---|---|---|
| 0x01 | Host → MCU | HELLO | version u8 (=1), token (0..64 B, checked by the bridge, ignored by the MCU) |
| 0x02 | Host → MCU | BLIT_BEGIN | x, y, w, h: u16 |
| 0x03 | Host → MCU | BLIT_DATA | RGB565 BE, **even** length, ≤ 512 B |
| 0x04 | Host → MCU | BLIT_END | — |
| 0x05 | Host → MCU | PROGRESS | elapsed_ms u32, duration_ms u32, playing u8 |
| 0x06 | Host → MCU | PING | — |
| 0x07 | Host → MCU | FILL | x, y, w, h, color (RGB565): u16 |
| 0x40 | Bridge → MCU | STATUS | state u8: 1 connecting to Wi-Fi, 2 setup portal, 3 waiting for the host. Only while no authenticated client is connected, every 2 s |
| 0x81 | MCU → Host | HELLO_ACK | proto u8, fw u16, width u16, height u16, window u16, max payload u16 |
| 0x82 | MCU → Host | CREDIT | bytes consumed since HELLO, u32 (cumulative) |
| 0x83 | MCU → Host | BUTTON | page u8 (always 0, the host tracks pages), index u8 (0..7, row-major; 0xFF for swipes), event u8 (below) |
| 0x84 | MCU → Host | LOG | UTF-8 text |

### BUTTON events

| Event | Meaning |
|---|---|
| 0 press | A finger touched a cell (the MCU paints its frame blue). The host runs no action |
| 1 release | The finger lifted. **The host runs the action here** |
| 2 long press | Held for 600 ms. Volume buttons repeat on the host until release |
| 3 cancel | The finger moved ≥ 30 px horizontally but not far enough to change page; no release follows, no action |
| 4 swipe left | Horizontal travel ≥ 60 px and larger than vertical; the host goes to the next page |
| 5 swipe right | Previous page |

If a swipe starts on a cell, the MCU first sends `press` for that cell, restores the frame once the
swipe begins and finally sends 3/4/5 instead of `release`. That is why actions run on release, not on
press.

## Session

1. The host sends HELLO and waits for HELLO_ACK (3 s). It sends nothing else in the meantime.
2. The MCU sends HELLO_ACK, re-initialises the display, clears the screen and draws the button frames.
   It resets the consumed-byte counter; the bytes of HELLO itself are not counted.
3. The host redraws the whole screen (cover, text, button icons, PROGRESS).
4. The host sends PING once a second. If the MCU receives no valid frame for **3 s** it shows the
   "not connected" screen, ignores everything until the next HELLO and stops sending CREDIT.
5. The MCU sends `LOG "boot"` at power-up.

Frames that arrive before HELLO are silently dropped. The host notices this as "CREDIT is not
advancing" (see below) and sends HELLO again. The same mechanism recovers the session after an MCU reset.

A blit the MCU cannot carry out is reported with a single LOG: rectangle off screen, BLIT_DATA without
BLIT_BEGIN (e.g. BEGIN lost to a CRC error), or more data than the rectangle. The remaining BLIT_DATA and the BLIT_END of that blit are then dropped silently. A blit
that is still short of pixels at BLIT_END is reported there, and so is a BLIT_END with no blit in
progress. Frames carry no sequence number, so if a blit's END *and* the next blit's BEGIN are both lost,
the second blit cannot be told apart from the first and goes unreported.

The MCU sends at most 4 LOG frames per second, because each one is written to the UART with a busy
wait. Any excess is dropped and reported once the second is over as `log: N messages suppressed`.

## Flow control (byte-based credit)

- The MCU receives the UART through DMA into an 8192 B circular buffer. The **window** in HELLO_ACK is
  8192 − 519 = 7673 B, large enough to cover Wi-Fi latency.
- After HELLO_ACK the host counts every byte it sends (`sent`). The difference to the last `consumed`
  value reported by the MCU never exceeds the window: `sent − consumed + len(next frame) ≤ window`.
- The MCU counts bytes as consumed once a frame has been handled. Bytes skipped while hunting for sync
  and frames with a bad CRC count as consumed too. It sends CREDIT after 2 KB have accumulated or 50 ms
  have passed, and repeats it every 500 ms even if unchanged.
- If the host has bytes in flight and sees no CREDIT progress for 5 s, it treats the link as stalled
  and reconnects.

A cumulative byte count is more robust than counting chunks. A lost CREDIT frame is made up for by the
next one, and because corrupt or skipped bytes are counted as well, the window never shrinks over time.
Since the window is smaller than the buffer, the buffer cannot overflow however long the MCU stays busy
(e.g. a 47 ms full-screen fill).

## Screen layout v1 (480×320, landscape)

The constants live in `firmware/include/ui.h` and `host/deskdeck/layout.py`; the two must match.

| Region | x, y, w, h | Drawn by |
|---|---|---|
| Cover art | 8, 8, 128, 128 | Host |
| Title / artist / album | 148, 8, 324, 112 | Host |
| Progress bar | 148, 124, 324, 6 | MCU (from PROGRESS; advances on its own while playing) |
| Time label | 148, 134, 160, 14 | Host |
| Page dots | 392, 134, 80, 14 (right-aligned) | Host |
| Button grid | 4, 152; cell 118×82; 4 columns × 2 rows | Frames: MCU, icons: host |
| Button icon area | cell inset by 6 px: 106×70 | Host (on page change: FILL + only the icon/label parts) |

The button frame sits 3 px inside the cell and is 2 px thick. It turns blue while pressed and returns
to its normal color on release.
