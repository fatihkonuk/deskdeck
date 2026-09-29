#include "blit.h"

#include "lcd.h"
#include "lcd_bus.h"

static struct {
    bool active;
    uint16_t x, y, w, h;
    uint32_t pos; /* pixels written */
} s_blit;

const char *blit_begin(uint16_t x, uint16_t y, uint16_t w, uint16_t h)
{
    s_blit.active = false;
    if (w == 0 || h == 0 || x + w > lcd_width() || y + h > lcd_height()) {
        return "blit: rectangle off screen";
    }
    s_blit = (typeof(s_blit)){ .active = true, .x = x, .y = y, .w = w, .h = h };
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
    if (!s_blit.active) {
        return "blit: DATA without BEGIN";
    }
    if (len & 1u) {
        return "blit: odd-length DATA";
    }

    const uint32_t total = (uint32_t)s_blit.w * s_blit.h;
    uint32_t pixels = len / 2u;
    if (s_blit.pos + pixels > total) {
        s_blit.active = false;
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
    const bool complete = s_blit.active && s_blit.pos == (uint32_t)s_blit.w * s_blit.h;
    s_blit.active = false;
    return complete ? NULL : "blit: data missing at END";
}
