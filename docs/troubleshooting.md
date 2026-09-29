# Troubleshooting

Problems that came up while building deskdeck, and what fixed them.

## Hardware

**The ESP8266 does not boot once wired to the STM32.**
GPIO15 (D8) is a boot strapping pin and must be LOW at boot. The NodeMCU has a 10 kΩ pull-down, so
nothing on the STM32 side may pull it up: PA10 (USART1 RX) is configured as a floating input on
purpose. Check your wiring and any modifications to `link_init()` first.

**The screen is garbled after plugging in USB, but fine after pressing RESET.**
5 V rises slowly and the ESP8266's Wi-Fi inrush current can sag the shared rail while the panel
initialises. The firmware waits 200 ms before initialising the display and re-initialises it on
every HELLO, so the screen recovers as soon as the Mac connects. If it persists, use a stronger 5 V
supply.

**Don't power the display from the ST-Link.**
Its 3.3 V pin cannot supply the display and the ESP. During development power the Blue Pill from its
micro-USB or 5V pin and connect only SWDIO, SWCLK and GND to the ST-Link.

**Touch is offset, mirrored or does nothing.**
Every panel needs its own calibration, and some shields use a different touch wiring. See
[hardware.md → Touch](hardware.md#touch).

**Resistive touch is noisy.** The driver averages the middle half of 8 samples, discards readings with
too much spread and uses a pressure threshold. After reading, the LCD pins are always restored to
outputs. If you change the touch code, keep that restore step.

## Toolchain

**PlatformIO's default ARM GCC doesn't run on Apple Silicon.** The default `toolchain-gccarmnoneeabi`
(GCC 7.2) is x86_64-only. `firmware/platformio.ini` pins GCC 12.3, which has native arm64 builds.

**The ESP8266 toolchain needs Rosetta 2 on Apple Silicon.** The Xtensa GCC that PlatformIO installs for
`espressif8266` is x86_64-only: `softwareupdate --install-rosetta --agree-to-license`.

**The Mac does not see the NodeMCU's serial port.** Check which USB-serial chip the board has
(CP2102 or CH340) and install its driver if macOS doesn't pick it up. Some micro-USB cables are
charge-only.

## Network

**`deskdeck.local` does not resolve.** On some mesh systems (seen on TP-Link Deco) the ESP8266's
multicast mDNS replies are never forwarded to the Mac, so the macOS system resolver fails.
`host/deskdeck/mdns.py` sends a legacy unicast query (RFC 6762 §6.7) instead, which gets a unicast
reply and works there. The last good IP is cached in `~/Library/Caches/deskdeck/device_ip` (dropped
again if that address stops completing the HELLO handshake, e.g. after the DHCP server hands it to
another host), and you can set a fixed `ip` in `config.toml` (give the ESP a DHCP reservation).

**The Mac cannot reach the device at all.** Guest networks are usually isolated from the main network.
Put the ESP on the same network as the Mac (IoT networks are often fine, but check). To change Wi-Fi,
hold the NodeMCU's FLASH button for 3 s and release: the credentials are erased and the
`DeskDeck-Setup` portal opens.

**The ESP stays on a distant mesh node.** The ESP8266 does not roam by itself. The bridge scans when
the signal stays below −70 dBm for 30 s and moves to a BSSID of the same SSID that is at least 10 dB
stronger (rescanning at most every 5 minutes).

**Never call `WiFi.begin(ssid, psk, channel, bssid)` with persistence on.** A persistent call wrote a
BSSID-locked record to flash and the ESP could not connect again until the credentials were erased and
re-entered. The roaming code wraps it in `WiFi.persistent(false)`.

**`WiFiClient` pitfalls on the ESP8266.**
- Its `bool` conversion stays true after `stop()` while unread data remains, so the bridge tracks
  client state with explicit flags.
- `write()` blocks for up to 5000 ms by default when lwIP can't queue a packet, stalling both
  directions. The bridge uses `setTimeout(10)` plus a 2 KB output buffer so no bytes are lost. The
  debug log on port 23 goes further and drops a line when TCP has no room for it, then reports how
  many lines were dropped.

**Periodic latency spikes on Wi-Fi.** On some networks the ESP8266 sees bursts of packet loss
(e.g. ~3 s every ~20 s), which turn into 1–5 s TCP round trips. The credit window (7.6 KB) and a 5 s
stall timeout on the host ride out most of them; a real disconnect is detected by TCP and triggers a
reconnect with backoff (1 → 10 s).

### Looking at the bridge

- Live log: `sleep 60 | nc deskdeck.local 23`. Stdin must stay open, otherwise the ESP stops writing.
- Status: send `deskdeck?` to UDP port 7789 and the bridge replies with uptime, reset reason, AP, RSSI,
  the last Wi-Fi events and the watchdog counter. The host logs this on every connect.
  ```sh
  echo -n 'deskdeck?' | nc -u -w1 deskdeck.local 7789
  ```
- Any other UDP payload to 7789 is echoed back, which is handy for measuring loss and latency.
- If Wi-Fi does not come back within 120 s, a watchdog restarts the ESP; the count survives the restart
  in RTC memory.

## macOS host

**`peer closed the connection during HELLO`.** The bridge closes any connection whose `HELLO` carries
the wrong token. Make sure `token` in `host/config.toml` matches `DESKDECK_TOKEN` in
`bridge/include/secrets.h`, and re-flash the bridge after changing it.

**The launchd service hangs silently (empty log).** If the project lives in a TCC-protected folder
(Desktop, Documents, Downloads), Python started by launchd blocks forever on a hidden permission
prompt when it opens files there. Keep the project somewhere like `~/Developer`. `launchd.sh` writes
the real path (`pwd -P`) into the plist, even if you call it through a symlink.

**`No route to host` only when running under launchd.** macOS asks for **Local Network** permission
per app. Enable Python under *System Settings → Privacy & Security → Local Network*. A Homebrew Python
upgrade can ask again.

**AppleScript actions fail under launchd.** Controlling other apps via `osascript` (e.g. toggling dark
mode through System Events) requires Automation permission, and the prompt may not appear for a
background agent. Run the action once from a terminal (`uv run deskdeck`) to grant it.

**Now playing stays empty.** Since macOS 15.4, apps can no longer read the MediaRemote framework
directly; deskdeck relies on [`media-control`](https://github.com/ungive/media-control), which works
around this. Run `media-control get` to check that it sees your player.
