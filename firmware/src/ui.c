#include "ui.h"

#include "lcd.h"
#include "proto.h"
#include "touch.h"

#define COLOR_BG        0x0000
#define COLOR_FRAME     RGB565(64, 64, 72)
#define COLOR_PRESSED   RGB565(80, 160, 255)
#define COLOR_BAR_BG    RGB565(48, 48, 56)
#define COLOR_BAR_FG    RGB565(230, 230, 230)
#define COLOR_ICON      RGB565(96, 96, 104)
#define COLOR_ALERT     RGB565(220, 40, 40)
#define COLOR_WAIT      RGB565(240, 180, 40)
#define COLOR_SETUP     RGB565(80, 160, 255)
#define COLOR_OK        RGB565(60, 200, 90)

#define TOUCH_PERIOD_MS     10
#define PROGRESS_PERIOD_MS  100
#define PRESS_SAMPLES       2   /* press confirmed after this many consecutive pressed samples */
#define RELEASE_SAMPLES     3   /* release confirmed after this many consecutive empty samples */
#define LONG_PRESS_MS       600
#define FRAME_INSET         3
/* Swipe: if the horizontal drift exceeds SWIPE_START_PX for SWIPE_SAMPLES consecutive samples the
 * button press is cancelled (so a single noisy sample cannot break a press). On release, the page
 * changes if the horizontal travel is longer than SWIPE_MIN_PX. */
#define SWIPE_START_PX      30
#define SWIPE_SAMPLES       2
#define SWIPE_MIN_PX        60

static ui_button_cb_t s_on_button;
static bool s_connected;
static int16_t s_offline_state = -1; /* offline state currently drawn, -1 if none */
static uint32_t s_last_touch_ms, s_last_progress_ms;

static struct {
    uint8_t pressed_count, released_count, moved_count;
    bool down;
    int8_t button; /* pressed button, -1 if none */
    bool long_sent, swiping;
    uint32_t down_ms;
    int16_t x0, y0, x, y; /* first and latest pressed position */
} s_touch = { .button = -1 };

static struct {
    uint32_t elapsed_ms, duration_ms, base_ms;
    bool playing;
    uint16_t drawn_px;
} s_prog;

static void draw_button_frame(uint8_t index, uint16_t color)
{
    const int16_t x = UI_GRID_X + (index % UI_GRID_COLS) * UI_CELL_W + FRAME_INSET;
    const int16_t y = UI_GRID_Y + (index / UI_GRID_COLS) * UI_CELL_H + FRAME_INSET;
    const int16_t w = UI_CELL_W - 2 * FRAME_INSET, h = UI_CELL_H - 2 * FRAME_INSET;
    lcd_draw_rect(x, y, w, h, color);
    lcd_draw_rect(x + 1, y + 1, w - 2, h - 2, color);
}

void ui_init(ui_button_cb_t on_button)
{
    s_on_button = on_button;
}

/* Signal bars: bottom-left corner (x, y), 4 bars. */
static void draw_bars(int16_t x, int16_t y, uint16_t color)
{
    for (int16_t i = 0; i < 4; i++) {
        lcd_fill_rect(x + i * 18, y - 12 * (i + 1), 12, 12 * (i + 1), color);
    }
}

static void draw_unplugged(int16_t cx, int16_t cy)
{
    lcd_fill_rect(cx - 80, cy - 20, 50, 40, COLOR_ICON);
    lcd_fill_rect(cx - 30, cy - 12, 16, 6, COLOR_ICON);
    lcd_fill_rect(cx - 30, cy + 6, 16, 6, COLOR_ICON);
    lcd_fill_rect(cx + 20, cy - 20, 50, 40, COLOR_ICON);
    lcd_fill_rect(cx + 22, cy - 12, 10, 6, COLOR_BG);
    lcd_fill_rect(cx + 22, cy + 6, 10, 6, COLOR_BG);
    for (int16_t d = -3; d <= 3; d++) {
        lcd_draw_line(cx - 50 + d, cy - 50, cx + 50 + d, cy + 50, COLOR_ALERT);
    }
}

static void draw_phone(int16_t cx, int16_t cy, uint16_t color)
{
    for (int16_t i = 0; i < 3; i++) {
        lcd_draw_rect(cx - 25 + i, cy - 45 + i, 50 - 2 * i, 90 - 2 * i, color);
    }
    lcd_fill_rect(cx - 5, cy + 32, 10, 5, color);
}

static void draw_laptop(int16_t cx, int16_t cy, uint16_t color)
{
    for (int16_t i = 0; i < 3; i++) {
        lcd_draw_rect(cx - 40 + i, cy - 30 + i, 80 - 2 * i, 52 - 2 * i, color);
    }
    lcd_fill_rect(cx - 52, cy + 24, 104, 7, color);
}

/* 0 unplugged connector (link to the host lost), 1 yellow bars (connecting to Wi-Fi),
 * 2 blue phone + bars (setup portal: join the DeskDeck-Setup network from a phone),
 * 3 green bars + grey laptop (Wi-Fi OK, waiting for the host). docs/protocol.md */
void ui_show_offline(uint8_t state)
{
    if (!s_connected && s_offline_state == state) {
        return;
    }
    s_connected = false;
    s_offline_state = state;
    s_touch = (typeof(s_touch)){ .button = -1 };

    const int16_t cx = lcd_width() / 2, cy = lcd_height() / 2;
    lcd_fill_screen(COLOR_BG);
    switch (state) {
    case BRIDGE_WIFI_CONNECTING:
        draw_bars(cx - 33, cy + 24, COLOR_WAIT);
        break;
    case BRIDGE_PORTAL:
        draw_phone(cx - 50, cy, COLOR_SETUP);
        draw_bars(cx + 10, cy + 24, COLOR_SETUP);
        break;
    case BRIDGE_WAITING_MAC:
        draw_bars(cx - 110, cy + 24, COLOR_OK);
        draw_laptop(cx + 50, cy, COLOR_ICON);
        break;
    default:
        draw_unplugged(cx, cy);
        break;
    }
}

void ui_show_connected(void)
{
    s_connected = true;
    s_offline_state = -1;
    s_touch = (typeof(s_touch)){ .button = -1 };
    s_prog = (typeof(s_prog)){ 0 };

    lcd_fill_screen(COLOR_BG);
    lcd_fill_rect(UI_PROGRESS_X, UI_PROGRESS_Y, UI_PROGRESS_W, UI_PROGRESS_H, COLOR_BAR_BG);
    for (uint8_t i = 0; i < UI_BUTTON_COUNT; i++) {
        draw_button_frame(i, COLOR_FRAME);
    }
}

static uint32_t progress_now_ms(uint32_t now_ms)
{
    uint32_t e = s_prog.elapsed_ms;
    if (s_prog.playing) {
        e += now_ms - s_prog.base_ms;
    }
    return e > s_prog.duration_ms ? s_prog.duration_ms : e;
}

static void draw_progress(uint32_t now_ms)
{
    uint16_t px = 0;
    if (s_prog.duration_ms) {
        px = (uint16_t)((uint64_t)progress_now_ms(now_ms) * UI_PROGRESS_W / s_prog.duration_ms);
    }
    if (px > s_prog.drawn_px) {
        lcd_fill_rect(UI_PROGRESS_X + s_prog.drawn_px, UI_PROGRESS_Y, px - s_prog.drawn_px, UI_PROGRESS_H, COLOR_BAR_FG);
    } else if (px < s_prog.drawn_px) {
        lcd_fill_rect(UI_PROGRESS_X + px, UI_PROGRESS_Y, s_prog.drawn_px - px, UI_PROGRESS_H, COLOR_BAR_BG);
    }
    s_prog.drawn_px = px;
}

void ui_set_progress(uint32_t elapsed_ms, uint32_t duration_ms, bool playing, uint32_t now_ms)
{
    s_prog.elapsed_ms = elapsed_ms;
    s_prog.duration_ms = duration_ms;
    s_prog.playing = playing;
    s_prog.base_ms = now_ms;
    if (s_connected) {
        draw_progress(now_ms);
    }
}

static int8_t button_at(int16_t x, int16_t y)
{
    if (x < UI_GRID_X || y < UI_GRID_Y) {
        return -1;
    }
    const int16_t col = (x - UI_GRID_X) / UI_CELL_W, row = (y - UI_GRID_Y) / UI_CELL_H;
    if (col >= UI_GRID_COLS || row >= UI_GRID_ROWS) {
        return -1;
    }
    return (int8_t)(row * UI_GRID_COLS + col);
}

static void touch_tick(uint32_t now_ms)
{
    touch_raw_t r;
    touch_read_raw(&r);
    const bool pressed = touch_pressed(&r);

    if (!s_touch.down) {
        s_touch.pressed_count = pressed ? s_touch.pressed_count + 1 : 0;
        if (s_touch.pressed_count < PRESS_SAMPLES) {
            return;
        }
        int16_t x, y;
        touch_map(&touch_cal_default, &r, lcd_width(), lcd_height(), &x, &y);
        s_touch.down = true;
        s_touch.released_count = 0;
        s_touch.down_ms = now_ms;
        s_touch.x0 = s_touch.x = x;
        s_touch.y0 = s_touch.y = y;
        s_touch.button = button_at(x, y);
        if (s_touch.button >= 0) {
            draw_button_frame((uint8_t)s_touch.button, COLOR_PRESSED);
            s_on_button(0, (uint8_t)s_touch.button, BUTTON_PRESS);
        }
        return;
    }

    if (pressed) {
        touch_map(&touch_cal_default, &r, lcd_width(), lcd_height(), &s_touch.x, &s_touch.y);
        if (!s_touch.swiping && !s_touch.long_sent) {
            const int16_t dx = s_touch.x - s_touch.x0;
            s_touch.moved_count = (dx >= SWIPE_START_PX || dx <= -SWIPE_START_PX) ? s_touch.moved_count + 1 : 0;
            if (s_touch.moved_count >= SWIPE_SAMPLES) {
                s_touch.swiping = true;
                if (s_touch.button >= 0) {
                    draw_button_frame((uint8_t)s_touch.button, COLOR_FRAME);
                }
            }
        }
    }

    s_touch.released_count = pressed ? 0 : s_touch.released_count + 1;
    if (s_touch.released_count >= RELEASE_SAMPLES) {
        const uint8_t index = s_touch.button >= 0 ? (uint8_t)s_touch.button : BUTTON_INDEX_NONE;
        if (s_touch.swiping) {
            const int16_t dx = s_touch.x - s_touch.x0, dy = s_touch.y - s_touch.y0;
            const int16_t adx = dx < 0 ? -dx : dx, ady = dy < 0 ? -dy : dy;
            uint8_t ev = BUTTON_CANCEL;
            if (adx >= SWIPE_MIN_PX && adx > ady) {
                ev = dx < 0 ? BUTTON_SWIPE_LEFT : BUTTON_SWIPE_RIGHT;
            }
            s_on_button(0, ev == BUTTON_CANCEL ? index : BUTTON_INDEX_NONE, ev);
        } else if (s_touch.button >= 0) {
            draw_button_frame(index, COLOR_FRAME);
            s_on_button(0, index, BUTTON_RELEASE);
        }
        s_touch = (typeof(s_touch)){ .button = -1 };
    } else if (s_touch.button >= 0 && !s_touch.long_sent && !s_touch.swiping &&
               now_ms - s_touch.down_ms >= LONG_PRESS_MS) {
        s_touch.long_sent = true;
        s_on_button(0, (uint8_t)s_touch.button, BUTTON_LONG);
    }
}

void ui_tick(uint32_t now_ms)
{
    if (!s_connected) {
        return;
    }
    if (now_ms - s_last_touch_ms >= TOUCH_PERIOD_MS) {
        s_last_touch_ms = now_ms;
        touch_tick(now_ms);
    }
    if (s_prog.playing && now_ms - s_last_progress_ms >= PROGRESS_PERIOD_MS) {
        s_last_progress_ms = now_ms;
        draw_progress(now_ms);
    }
}
