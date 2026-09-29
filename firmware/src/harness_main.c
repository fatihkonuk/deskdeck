/* Bring-up harness (env:harness): display test patterns and touch.
 * Upload: pio run -e harness -t upload
 * g_probe is changed over SWD (tools/lcd_ctl.py); when req_seq increments the display is
 * re-initialised and the selected pattern is drawn. Touch state is in g_touch (tools/touch_ctl.py).
 * LED: slow blink = running. */
#include "stm32f1xx_hal.h"

#include "board.h"
#include "lcd.h"
#include "lcd_bus.h"
#include "pins.h"
#include "touch.h"
#include "touch_diag.h"

#define PROBE_MAGIC 0x4252504Cu /* "LPRB" */

enum { PATTERN_TEST_CARD = 0, PATTERN_LINES = 1, PATTERN_GRADIENTS = 2, PATTERN_ROTATIONS = 3, PATTERN_TOUCH_DIAG = 4, PATTERN_TOUCH = 5, PATTERN_TOUCH_CAL = 6 };

/* Layout must match tools/lcd_ctl.py. */
typedef struct {
    uint32_t magic;
    uint32_t req_seq;  /* incremented by the host */
    uint32_t done_seq; /* set to req_seq by the firmware when drawing is done */
    uint8_t madctl;
    uint8_t invert;
    uint8_t pattern;
    uint8_t reserved;
    uint32_t sysclk_hz;
    uint32_t fill_us; /* duration of the last full-screen black fill */
} lcd_probe_t;

__attribute__((used)) volatile lcd_probe_t g_probe = {
    .magic = PROBE_MAGIC,
    .req_seq = 1,
    .madctl = MADCTL_MV | MADCTL_BGR,
    .pattern = PATTERN_TEST_CARD,
};

static void timed_fill_black(void)
{
    uint32_t start = DWT->CYCCNT;
    lcd_fill_screen(0x0000);
    g_probe.fill_us = (DWT->CYCCNT - start) / (SystemCoreClock / 1000000u);
}

/* Orientation: large white square top-left, small one top-right.
 * Color order/inversion: red, green, blue blocks.
 * Data bus: a grey ramp that does not darken smoothly from left to right means a bit error.
 * Resolution: the 1 px white border must be visible on all four edges. */
static void draw_test_card(void)
{
    const int16_t w = lcd_width(), h = lcd_height();

    timed_fill_black();
    lcd_draw_rect(0, 0, w, h, 0xFFFF);
    lcd_fill_rect(0, 0, 40, 40, 0xFFFF);
    lcd_fill_rect(w - 20, 0, 20, 20, 0xFFFF);

    const int16_t bw = w / 5, bh = h / 4, by = h / 4;
    lcd_fill_rect(bw * 1, by, bw - 8, bh, RGB565(255, 0, 0));
    lcd_fill_rect(bw * 2, by, bw - 8, bh, RGB565(0, 255, 0));
    lcd_fill_rect(bw * 3, by, bw - 8, bh, RGB565(0, 0, 255));

    const int16_t steps = 32, sw = (w - 32) / steps, gy = h * 5 / 8;
    for (int16_t i = 0; i < steps; i++) {
        uint16_t c = (uint16_t)((i << 11) | ((i * 2) << 5) | i);
        lcd_fill_rect(16 + i * sw, gy, sw, h / 8, c);
    }
}

static void draw_lines(void)
{
    const int16_t w = lcd_width(), h = lcd_height();
    static const uint16_t colors[] = { 0xFFFF, 0xF800, 0x07E0, 0x001F, 0xFFE0, 0x07FF, 0xF81F };

    timed_fill_black();
    uint32_t n = 0;
    for (int16_t x = 0; x < w; x += 24) {
        lcd_draw_line(w / 2, h / 2, x, 0, colors[n++ % 7]);
        lcd_draw_line(w / 2, h / 2, w - 1 - x, h - 1, colors[n++ % 7]);
    }
    for (int16_t y = 0; y < h; y += 24) {
        lcd_draw_line(w / 2, h / 2, w - 1, y, colors[n++ % 7]);
        lcd_draw_line(w / 2, h / 2, 0, h - 1 - y, colors[n++ % 7]);
    }
    lcd_draw_rect(0, 0, w, h, 0xFFFF);
}

/* Four rows, each starting with as many small white squares as its row number:
 * 1 red, 2 green (64 steps), 3 blue, 4 grey. A bit error on the data bus corrupts both colors
 * and coordinates: rows swap places and black gaps appear between them. */
static void draw_gradients(void)
{
    const int16_t w = lcd_width(), h = lcd_height();
    const int16_t row_h = h / 5, gap = row_h / 5, x0 = 64, span = w - x0 - 8;

    timed_fill_black();
    for (int16_t row = 0; row < 4; row++) {
        const int16_t y = gap + row * (row_h + gap);
        for (int16_t k = 0; k <= row; k++) {
            lcd_fill_rect(4 + k * 10, y + row_h / 2 - 3, 6, 6, 0xFFFF);
        }
        const int16_t steps = row == 1 ? 64 : 32, sw = span / steps;
        for (int16_t i = 0; i < steps; i++) {
            uint16_t c;
            switch (row) {
            case 0: c = (uint16_t)(i << 11); break;
            case 1: c = (uint16_t)(i << 5); break;
            case 2: c = (uint16_t)i; break;
            default: c = (uint16_t)((i << 11) | ((i * 2) << 5) | i); break;
            }
            lcd_fill_rect(x0 + i * sw, y, sw, row_h, c);
        }
    }
}

/* MADCTL only changes write addressing; earlier drawing stays in GRAM. So the four rotations are
 * drawn on top of each other without clearing: 0 red, 1 green, 2 blue, 3 yellow. Each rotation draws
 * a large square in its own top-left, a small square along its top edge to the right, a frame r*4 px
 * inset and a line from its top-left towards the centre. When rotation works: the large squares sit
 * in four different corners, each small square is clockwise from its large one, the frames nest
 * neatly and the lines form an X in the centre. */
static void draw_rotations(void)
{
    static const uint16_t colors[4] = { 0xF800, 0x07E0, 0x001F, 0xFFE0 };

    timed_fill_black();
    for (uint8_t r = 0; r < 4; r++) {
        lcd_set_rotation(r);
        const int16_t w = lcd_width(), h = lcd_height(), in = r * 4;
        lcd_draw_rect(in, in, w - 2 * in, h - 2 * in, colors[r]);
        lcd_fill_rect(20, 20, 40, 40, colors[r]);
        lcd_fill_rect(w - 100, 20, 20, 20, colors[r]);
        lcd_draw_line(60, 60, w / 2, h / 2, colors[r]);
    }
    lcd_set_madctl(g_probe.madctl);
}

/* ---- Touch test: draws where touched; PATTERN_TOUCH_CAL first calibrates with 4 targets ---- */

#define TOUCH_MAGIC    0x48435554u /* "TUCH" */
#define CAL_INSET      30
#define STATE_DRAW     4           /* 0..3: calibration target */
#define PRESS_SKIP     3           /* the first samples of a press are unstable */
#define PRESS_SAMPLES  8
#define RELEASE_SAMPLES 5
#define CLEAR_BOX      36          /* red box top-right: touching it clears the screen */

/* Layout must match tools/touch_ctl.py. */
typedef struct {
    uint32_t magic;
    uint32_t samples;
    uint16_t raw_x, raw_y, raw_z;
    uint8_t pressed;
    uint8_t state;
    uint8_t cal_valid;
    uint8_t swap_xy;
    uint16_t reserved;
    int16_t raw_x_a, raw_x_b, raw_y_a, raw_y_b;
    int16_t last_sx, last_sy;
} touch_probe_t;

__attribute__((used)) volatile touch_probe_t g_touch = { .magic = TOUCH_MAGIC };

static struct {
    touch_cal_t cal;
    touch_raw_t pts[4];
    uint32_t acc_x, acc_y;
    uint8_t press_count, release_count;
    bool wait_release, have_prev;
    int16_t prev_x, prev_y;
} s_tt;

static void draw_target(uint8_t k, uint16_t color)
{
    const int16_t w = lcd_width(), h = lcd_height();
    const int16_t x = (k == 1 || k == 2) ? w - 1 - CAL_INSET : CAL_INSET;
    const int16_t y = k >= 2 ? h - 1 - CAL_INSET : CAL_INSET;
    lcd_draw_line(x - 14, y, x + 14, y, color);
    lcd_draw_line(x, y - 14, x, y + 14, color);
    lcd_fill_rect(x - 2, y - 2, 5, 5, color);
}

static void draw_touch_canvas(void)
{
    const int16_t w = lcd_width(), h = lcd_height();
    lcd_fill_screen(0x0000);
    lcd_draw_rect(0, 0, w, h, 0x7BEF);
    lcd_fill_rect(w - CLEAR_BOX, 0, CLEAR_BOX, CLEAR_BOX, 0xF800);
}

static void publish_cal(void)
{
    g_touch.swap_xy = s_tt.cal.swap_xy;
    g_touch.raw_x_a = s_tt.cal.raw_x_a;
    g_touch.raw_x_b = s_tt.cal.raw_x_b;
    g_touch.raw_y_a = s_tt.cal.raw_y_a;
    g_touch.raw_y_b = s_tt.cal.raw_y_b;
}

static void touch_test_start(bool calibrate)
{
    s_tt = (typeof(s_tt)){ 0 };
    if (!calibrate) {
        s_tt.cal = touch_cal_default;
        publish_cal();
        g_touch.cal_valid = 1;
        g_touch.state = STATE_DRAW;
        draw_touch_canvas();
        return;
    }
    g_touch.state = 0;
    g_touch.cal_valid = 0;
    lcd_fill_screen(0x0000);
    draw_target(0, 0xFFFF);
}

static void finish_calibration(void)
{
    const int16_t w = lcd_width(), h = lcd_height();
    g_touch.cal_valid = touch_calibrate(s_tt.pts, w, h, CAL_INSET, &s_tt.cal);
    publish_cal();
    if (g_touch.cal_valid) {
        g_touch.state = STATE_DRAW;
        draw_touch_canvas();
    } else {
        touch_test_start(true); /* axes could not be told apart: start over */
    }
}

static void touch_test_step(void)
{
    touch_raw_t r;
    touch_read_raw(&r);
    const bool pressed = touch_pressed(&r);
    g_touch.raw_x = r.x;
    g_touch.raw_y = r.y;
    g_touch.raw_z = r.z;
    g_touch.pressed = pressed;
    g_touch.samples++;

    if (g_touch.state < STATE_DRAW) {
        if (s_tt.wait_release) {
            s_tt.release_count = pressed ? 0 : s_tt.release_count + 1;
            if (s_tt.release_count >= RELEASE_SAMPLES) {
                s_tt.wait_release = false;
                if (++g_touch.state < STATE_DRAW) {
                    draw_target(g_touch.state, 0xFFFF);
                } else {
                    finish_calibration();
                }
            }
        } else if (!pressed) {
            s_tt.press_count = 0;
            s_tt.acc_x = s_tt.acc_y = 0;
        } else if (++s_tt.press_count > PRESS_SKIP) {
            s_tt.acc_x += r.x;
            s_tt.acc_y += r.y;
            if (s_tt.press_count == PRESS_SKIP + PRESS_SAMPLES) {
                touch_raw_t *p = &s_tt.pts[g_touch.state];
                p->x = (uint16_t)(s_tt.acc_x / PRESS_SAMPLES);
                p->y = (uint16_t)(s_tt.acc_y / PRESS_SAMPLES);
                draw_target(g_touch.state, 0x0000);
                s_tt.press_count = s_tt.release_count = 0;
                s_tt.acc_x = s_tt.acc_y = 0;
                s_tt.wait_release = true;
            }
        }
        return;
    }

    if (!pressed) {
        s_tt.have_prev = false;
        return;
    }
    const int16_t w = lcd_width(), h = lcd_height();
    int16_t x, y;
    touch_map(&s_tt.cal, &r, w, h, &x, &y);
    g_touch.last_sx = x;
    g_touch.last_sy = y;
    if (x >= w - CLEAR_BOX && y < CLEAR_BOX) {
        draw_touch_canvas();
        s_tt.have_prev = false;
        return;
    }
    if (s_tt.have_prev) {
        lcd_draw_line(s_tt.prev_x, s_tt.prev_y, x, y, 0xFFFF);
    }
    lcd_fill_rect(x - 1, y - 1, 3, 3, 0xFFFF);
    s_tt.prev_x = x;
    s_tt.prev_y = y;
    s_tt.have_prev = true;
}

int main(void)
{
    board_init();
    lcd_bus_init();
    touch_init();

    g_probe.sysclk_hz = SystemCoreClock;

    uint32_t last_blink = 0, last_touch = 0;
    for (;;) {
        uint32_t req = g_probe.req_seq;
        if (req != g_probe.done_seq) {
            lcd_init(g_probe.madctl, g_probe.invert);
            if (g_probe.pattern == PATTERN_LINES) {
                draw_lines();
            } else if (g_probe.pattern == PATTERN_GRADIENTS) {
                draw_gradients();
            } else if (g_probe.pattern == PATTERN_ROTATIONS) {
                draw_rotations();
            } else if (g_probe.pattern == PATTERN_TOUCH_DIAG) {
                touch_diag_run();
                draw_test_card();
            } else if (g_probe.pattern == PATTERN_TOUCH || g_probe.pattern == PATTERN_TOUCH_CAL) {
                touch_test_start(g_probe.pattern == PATTERN_TOUCH_CAL);
            } else {
                draw_test_card();
            }
            g_probe.done_seq = req;
        }
        if ((g_probe.pattern == PATTERN_TOUCH || g_probe.pattern == PATTERN_TOUCH_CAL) && HAL_GetTick() - last_touch >= 10) {
            last_touch = HAL_GetTick();
            touch_test_step();
        }
        if (HAL_GetTick() - last_blink >= 500) {
            last_blink = HAL_GetTick();
            board_led_toggle();
        }
    }
}
