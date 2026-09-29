/* Frame exchange over USART1 (PA9 TX, PA10 RX).
 * Reception goes through DMA1 channel 5 into a circular buffer; frames are parsed in place (no copy).
 * Flow control is byte based: the host keeps the difference between the bytes it sent since HELLO
 * and the consumed-byte count the MCU reports with CREDIT below LINK_WINDOW_BYTES. That way the
 * buffer never overflows, no matter how long the MCU stays busy. */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "proto.h"

#define LINK_BAUD         921600u
#define LINK_RX_BUF_SIZE  8192u /* must be a power of two; the window must cover Wi-Fi latency */
#define LINK_WINDOW_BYTES (LINK_RX_BUF_SIZE - PROTO_MAX_FRAME)

/* A payload sitting in the circular buffer: at most two pieces (a, then b). */
typedef struct {
    const uint8_t *a;
    const uint8_t *b;
    uint16_t a_len, b_len;
} link_span_t;

static inline uint16_t link_span_len(const link_span_t *s) { return s->a_len + s->b_len; }

static inline uint8_t link_span_u8(const link_span_t *s, uint16_t i)
{
    return i < s->a_len ? s->a[i] : s->b[i - s->a_len];
}

static inline uint16_t link_span_u16(const link_span_t *s, uint16_t i)
{
    return (uint16_t)(link_span_u8(s, i) | (link_span_u8(s, i + 1) << 8));
}

static inline uint32_t link_span_u32(const link_span_t *s, uint16_t i)
{
    return link_span_u16(s, i) | ((uint32_t)link_span_u16(s, i + 2) << 16);
}

typedef void (*link_handler_t)(uint8_t type, const link_span_t *payload);

/* Counters readable over SWD (debugging). */
typedef struct {
    uint32_t frames;
    uint32_t crc_errors;
    uint32_t bad_len;
    uint32_t skipped; /* bytes skipped while hunting for sync */
    uint32_t consumed; /* bytes consumed since HELLO (the CREDIT value) */
} link_stats_t;

extern volatile link_stats_t g_link_stats;

void link_init(void);

/* Handles complete frames in the buffer (at most max_frames). The consumed-byte counter is reset
 * after a HELLO frame. */
void link_poll(link_handler_t handler, uint32_t max_frames);

/* Sends CREDIT when the consumed count changed and either 2 KB accumulated or 50 ms passed; if it
 * did not change, repeats the same value every 500 ms (harmless because it is cumulative, and it
 * makes up for a lost one). Call only while connected: staying silent otherwise lets the host
 * notice the stall and send HELLO again. */
void link_credit_tick(uint32_t now_ms);

void link_send(uint8_t type, const void *payload, uint16_t len);
/* Sends a LOG frame. At most 4 per second: the excess is dropped and counted, and link_log_tick
 * reports the count once the second is over. */
void link_log(const char *text);
void link_log_tick(uint32_t now_ms);
