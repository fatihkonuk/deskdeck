# Hardware

The single source of truth for pins in software is `firmware/include/pins.h`.

## Bill of materials

| Part | Notes |
|---|---|
| STM32F103C8 "Blue Pill" | 72 MHz Cortex-M3, 20 KB RAM, 64 KB flash. Drives the display and reads touch |
| ESP8266 NodeMCU (v2) | Wi-Fi ↔ UART bridge. Has its own USB-serial chip (CP2102 or CH340) and 3.3 V regulator |
| 3.5" TFT, Arduino Uno shield, 8-bit parallel, 480×320 | The panel used here is marked `HSD035264 D7`. 4-wire resistive touch. Any MIPI DCS controller (ILI9486/9488, ST7796, R61581, HX8357…) should work |
| ST-Link V2 (or clone) | SWD programming of the STM32 (PA13/PA14) |
| 5 V USB power supply | The only cable the finished device needs |
| Jumper wires | ~20 female–female |

Power: Blue Pill 5V pin, TFT 5V and NodeMCU VIN share one 5 V supply. **All grounds must be common.**

## TFT → STM32

| TFT | STM32 | Notes |
|---|---|---|
| 5V | 5V | Uno shields need 5 V (they have their own 3.3 V regulator) |
| GND | GND | |
| LCD_D0..D7 | PA0..PA7 | Straight through: PAn = LCD_Dn |
| LCD_RD | PB7 | Driven high at init and never lowered |
| LCD_WR | PB6 | |
| LCD_RS | PB1 | ADC IN9 (doubles as touch XM) |
| LCD_CS | PB0 | ADC IN8 (doubles as touch YP) |
| LCD_RST | PB9 | |

Because the data bus is exactly PA0..PA7, a byte is written with a single store:
`GPIOA->BSRR = 0x00FF0000 | b` (the low half sets, the high half resets, and set wins, RM0008 §9.2.5).

> [!NOTE]
> Wire labels on cheap shields and cables are not always right. If the gradients pattern
> (`tools/lcd_ctl.py --pattern gradients`) shows rows swapped with black gaps between them, two data
> lines are crossed. Fix the wiring rather than remapping bits in software.

### The shield is write-only

These 3.5" Uno shields carry **2 × 74LVC245A**: one-way 5 V → 3.3 V level shifters for 13 signals.
They are the well-known "write-only" shields: the controller ID cannot be read back (see the
MCUFRIEND_kbv documentation), so the controller is identified by trial with test patterns instead.

Safety rule: **RD (PB7) is set high at init and never lowered, and the data bus is always an output.**
That way the shield never drives PA0..PA7, which are not 5 V tolerant, whatever its design.
There is no LCD read code in the firmware on purpose.

### Display init

The generic MIPI DCS sequence is enough: `SWRESET`, `SLPOUT`, `COLMOD 0x55` (RGB565), `MADCTL`,
`INVOFF`, `DISPON`. No chip-specific power or gamma setup.

| Setting | Value |
|---|---|
| Landscape MADCTL | `0x28` (MV \| BGR) |
| Rotations 0..3 | MADCTL `0x48` / `0x28` / `0x88` / `0xE8` |
| Inversion | off |
| Full-screen fill (320×480, one color, 72 MHz) | 47 ms |

If your panel shows wrong colors or a mirrored image, experiment with the harness:
`tools/lcd_ctl.py --madctl 0x28 --invert 1 --pattern card`.

## Touch

On Uno shields the 4-wire resistive panel shares pins with the LCD. The two lines that must be read
as analog (XM and YP) have to land on STM32 pins with an ADC. There are two common mappings:

| Mapping | XP | XM | YP | YM | Works with the wiring above |
|---|---|---|---|---|---|
| A | D0 (PA0) | RS (PB1) | CS (PB0) | D1 (PA1) | ✅ as is |
| B | D6 (PA6) | RS (PB1) | WR (PB6) | D7 (PA7) | ❌ PB6 has no ADC: swap the WR and CS wires (WR→PB0, CS→PB6) and update `pins.h` |

The firmware assumes **mapping A**. To find out which one your shield uses, flash the harness and run
the diagnostic (don't touch the screen while it runs):

```sh
cd firmware && pio run -e harness -t upload && cd ..
python3 tools/touch_diag.py
```

It drives each candidate pin LOW in turn with the others pulled up and reports which pairs are
connected through the panel. Mapping A shows exactly PA0↔PB1 and PA1↔PB0, in both directions.

### Calibration

Every panel is different. Calibrate with four targets (a stylus tip works best):

```sh
python3 tools/touch_ctl.py --cal     # touch the four crosses in turn
python3 tools/touch_ctl.py --watch   # live raw values and the resulting calibration
```

The calibration detects whether the panel's axes are swapped relative to the display. Copy the
resulting values into `touch_cal_default` in `firmware/src/touch.c`, then rebuild the main firmware.
In drawing mode the harness draws wherever you touch; the red box in the top-right corner clears the
screen.

The pressure threshold is `Z_MIN = 300` (12-bit) in `touch.c`. Readings take 8 samples per channel and
average the middle half; a reading whose middle samples spread more than 60 counts is discarded.
The UI polls touch every 10 ms.

## ESP8266 (NodeMCU) ↔ STM32

| NodeMCU | GPIO | STM32 / power | Notes |
|---|---|---|---|
| D7 | GPIO13 | PA9 (USART1 TX) | ESP RX after `Serial.swap()` |
| D8 | GPIO15 | PA10 (USART1 RX) | ESP TX after `Serial.swap()`. **Boot strapping pin**, see below |
| VIN | — | 5V | The on-board regulator makes 3.3 V |
| GND | — | GND | Common ground is required |
| D4 | GPIO2 | — | Optional `Serial1` debug TX (the on-board LED is on this pin too) |

Both sides use 3.3 V logic, so they connect directly. The UART runs at 921600 baud (measured
throughput 88.4 KB/s).

`Serial.swap()` moves UART0 to GPIO13/GPIO15, so the ROM boot messages at 74880 baud and the traffic
of the USB-serial chip never reach the STM32. The STM32 still drops anything that fails the CRC.

GPIO15 must be LOW when the ESP boots (the NodeMCU has a 10 kΩ pull-down). PA10 is therefore
configured as a **floating input, without a pull-up**. If the ESP won't boot, check this first.

## Wired test setup (no Wi-Fi)

The protocol can be tested without the bridge firmware, using the NodeMCU's USB-serial chip as a
plain USB-TTL adapter:

1. Connect NodeMCU **RST** to **GND**. The ESP stays in reset; only the USB-serial chip is active.
2. NodeMCU **TX** (GPIO1) → STM32 **PA9** (STM32 TX).
3. NodeMCU **RX** (GPIO3) → STM32 **PA10** (STM32 RX).
   The labels are from the ESP's point of view, so the wiring looks reversed. It is correct.
4. NodeMCU **GND** → STM32 **GND**.
5. NodeMCU **VIN** → STM32 **5V**, and plug the NodeMCU into the Mac. Leave the Blue Pill's own USB
   unplugged: never connect VIN while two supplies are present.
6. `cd host && uv run deskdeck-wiretest --image some-cover.png`

## Bring-up tools

All tools talk to the running firmware over SWD with `openocd` (they never halt the target). They need
the `harness` firmware to be flashed: `cd firmware && pio run -e harness -t upload`.

| Tool | Purpose |
|---|---|
| `tools/chip_probe.sh` | Reads the chip ID, CPUID and flash size. `--flash-test` checks whether a "C8" part really has 128 KB |
| `tools/lcd_ctl.py` | Changes MADCTL, inversion and the test pattern (`card`, `lines`, `gradients`, `rotations`) and reports the full-screen fill time |
| `tools/touch_diag.py` | Finds the touch wiring (mapping A or B) |
| `tools/touch_ctl.py` | Touch drawing test and 4-point calibration |

Useful chip ID values:

| Register | Value | Meaning |
|---|---|---|
| DBGMCU_IDCODE (0xE0042000) | DEV_ID `0x410` | Medium-density F103 (C8/CB) |
| CPUID (0xE000ED00) | `0x411FC231` | Genuine ST (Cortex-M3 r1p1); `0x412FC231` is likely a CKS32 clone |
| F_SIZE (0x1FFFF7E0) | `0x0040` / `0x0080` | 64 KB / 128 KB |

Many C8 parts accept writes above 64 KB, but ST does not guarantee it. The build keeps the official
64 KB limit; the main firmware currently uses about 9 KB of flash and 8.2 KB of RAM.

## Reserved pins

| Pin | Use |
|---|---|
| PA13 / PA14 | SWD (ST-Link). Don't use for anything else |
| PA9 / PA10 | USART1 to the ESP8266 |
| PB2 | BOOT1 |
| PC13 | On-board LED (active low). Slow blink: not connected, fast blink: connected |
| PC14 / PC15 | Weak outputs |
| PA11 / PA12 | USB, unused |
