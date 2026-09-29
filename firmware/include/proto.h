/* Host ↔ STM32 protocol. Single source of truth: docs/protocol.md (must match host/deskdeck/protocol.py).
 * Frame: A5 5A | type (1) | length (2, LE) | payload | CRC16-CCITT-FALSE (2, LE; over type..payload) */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PROTO_VERSION     1
#define FW_VERSION        0x0001

#define PROTO_SYNC0       0xA5
#define PROTO_SYNC1       0x5A
#define PROTO_HEADER_LEN  5
#define PROTO_CRC_LEN     2
#define PROTO_MAX_PAYLOAD 512
#define PROTO_MAX_FRAME   (PROTO_HEADER_LEN + PROTO_MAX_PAYLOAD + PROTO_CRC_LEN)

enum {
    /* Host → MCU */
    MSG_HELLO      = 0x01, /* version u8, token (checked by the bridge, ignored by the MCU) */
    MSG_BLIT_BEGIN = 0x02, /* x, y, w, h: u16 */
    MSG_BLIT_DATA  = 0x03, /* RGB565 big-endian, even length, ≤ 512 B */
    MSG_BLIT_END   = 0x04,
    MSG_PROGRESS   = 0x05, /* elapsed_ms u32, duration_ms u32, playing u8 */
    MSG_PING       = 0x06,
    MSG_FILL       = 0x07, /* x, y, w, h, color: u16 */

    /* Bridge (ESP8266) → MCU. Only while no authenticated client is connected, every 2 s. */
    MSG_STATUS     = 0x40, /* state u8: BRIDGE_* */

    /* MCU → Host */
    MSG_HELLO_ACK  = 0x81, /* proto u8, fw u16, width u16, height u16, window u16, max payload u16 */
    MSG_CREDIT     = 0x82, /* bytes consumed since HELLO u32 (cumulative) */
    MSG_BUTTON     = 0x83, /* page u8 (always 0 in v1, the host tracks pages), index u8 (0xFF on swipe), event u8 */
    MSG_LOG        = 0x84, /* UTF-8 text */
};

/* CANCEL: the finger moved, but not far enough to change page; the press ends with no action
 * (no RELEASE follows). SWIPE_*: direction the finger moved; swiping left means next page. */
enum {
    BUTTON_PRESS = 0, BUTTON_RELEASE = 1, BUTTON_LONG = 2,
    BUTTON_CANCEL = 3, BUTTON_SWIPE_LEFT = 4, BUTTON_SWIPE_RIGHT = 5,
};
#define BUTTON_INDEX_NONE 0xFF
enum { BRIDGE_WIFI_CONNECTING = 1, BRIDGE_PORTAL = 2, BRIDGE_WAITING_MAC = 3 };

/* Whether a received header can start a frame: a known type with a length that fits it. Checked
 * before waiting for the payload, so an A5 5A that happens to appear in pixel data (after a CRC error
 * threw the parser off) is skipped instead of making it wait for up to 512 bytes of "payload". */
static inline bool proto_header_ok(uint8_t type, uint16_t len)
{
    switch (type) {
    case MSG_HELLO:      return len >= 1 && len <= PROTO_MAX_PAYLOAD;
    case MSG_BLIT_BEGIN: return len == 8;
    case MSG_BLIT_DATA:  return len > 0 && len <= PROTO_MAX_PAYLOAD && (len & 1u) == 0;
    case MSG_BLIT_END:   return len == 0;
    case MSG_PROGRESS:   return len == 9;
    case MSG_PING:       return len == 0;
    case MSG_FILL:       return len == 10;
    case MSG_STATUS:     return len == 1;
    default:             return false;
    }
}

uint16_t crc16_ccitt(uint16_t crc, const uint8_t *data, size_t n);
