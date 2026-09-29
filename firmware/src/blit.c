#include "blit.h"

#include "lcd.h"
#include "lcd_bus.h"

/* After an error the rest of that blit is discarded quietly: one rejected 128×128 cover would otherwise
 * produce 64 "DATA without BEGIN" LOG frames, and the MCU busy-waits on the UART to send each one. */
typedef enum { BLIT_IDLE, BLIT_ACTIVE, BLIT_DISCARD } blit_state_t;

static struct {
    blit_state_t state;
    uint16_t x, y, w, h;
    uint32_t pos; /* pixels written */
} s_blit;

const char *blit_begin(uint16_t x, uint16_t y, uint16_t w, uint16_t h)
{
    if (w == 0 || h == 0 || x + w > lcd_width() || y + h > lcd_height()) {
        s_blit.state = BLIT_DISCARD;
        return "blit: rectangle off screen";
    }
    s_blit = (typeof(s_blit)){ .state = BLIT_ACTIVE, .x = x, .y = y, .w = w, .h = h };
    return NULL;
}

/* Writes n bytes of span starting at byte_off (handles both pieces of the circular buffer). */
static void write_span(const link_span_t *s, uint16_t byte_off, uint16_t n)
{
    if (byte_off < s->a_len) {
        uint16_t k = s->a_len - byte_off < n ? s->a_len - byte_off : n;
        lcd_write_bytes(s->a + byte_off, k);
        byte_off += k;
        n -= k;
    }
    if (n) {
        lcd_write_bytes(s->b + (byte_off - s->a_len), n);
    }
}

const char *blit_data(const link_span_t *data)
{
    const uint16_t len = link_span_len(data);
    if (s_blit.state == BLIT_DISCARD) {
        return NULL; /* already reported */
    }
    if (s_blit.state == BLIT_IDLE) {
        s_blit.state = BLIT_DISCARD; /* e.g. BEGIN lost to a CRC error: report once, drop until END */
        return "blit: DATA without BEGIN";
    }
    if (len & 1u) {
        s_blit.state = BLIT_DISCARD;
        return "blit: odd-length DATA";
    }

    const uint32_t total = (uint32_t)s_blit.w * s_blit.h;
    uint32_t pixels = len / 2u;
    if (s_blit.pos + pixels > total) {
        s_blit.state = BLIT_DISCARD;
        return "blit: more data than the rectangle";
    }

    uint16_t off = 0;
    while (pixels) {
        const uint16_t row = (uint16_t)(s_blit.pos / s_blit.w);
        const uint16_t col = (uint16_t)(s_blit.pos % s_blit.w);
        uint32_t seg;
        if (col != 0) {
            seg = s_blit.w - col;
            lcd_begin_write(s_blit.x + col, s_blit.y + row, seg, 1);
        } else {
            seg = total - s_blit.pos;
            lcd_begin_write(s_blit.x, s_blit.y + row, s_blit.w, s_blit.h - row);
        }
        if (seg > pixels) {
            seg = pixels;
        }
        write_span(data, off, (uint16_t)(seg * 2u));
        lcd_end_write();
        off += (uint16_t)(seg * 2u);
        s_blit.pos += seg;
        pixels -= seg;
    }
    return NULL;
}

const char *blit_end(void)
{
    const blit_state_t state = s_blit.state;
    s_blit.state = BLIT_IDLE;
    if (state == BLIT_DISCARD) {
        return NULL; /* already reported */
    }
    if (state == BLIT_IDLE) {
        return "blit: END without BEGIN";
    }
    return s_blit.pos == (uint32_t)s_blit.w * s_blit.h ? NULL : "blit: data missing at END";
}
