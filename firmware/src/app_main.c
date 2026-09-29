/* deskdeck firmware (env:bluepill). Handles frames from the host, draws the UI and sends
 * button events. Protocol: docs/protocol.md
 * LED: slow blink = not connected, fast blink = connected. */
#include "stm32f1xx_hal.h"

#include "blit.h"
#include "board.h"
#include "lcd.h"
#include "lcd_bus.h"
#include "link.h"
#include "touch.h"
#include "ui.h"

#define PING_TIMEOUT_MS     3000
#define FRAMES_PER_POLL     8   /* give touch a turn in between */
/* 5V rises slowly when USB is plugged in and the panel may become ready later than the STM32
 * (without this the image was garbled on cold boot until RESET was pressed). */
#define POWERUP_SETTLE_MS   200

static bool s_connected;
static uint32_t s_last_rx_ms;

static void log_if(const char *err)
{
    if (err) {
        link_log(err);
    }
}

/* Init + orientation. Repeated on every HELLO: at power-up the ESP8266's Wi-Fi current can sag
 * the shared 5V rail and corrupt the panel's settings; the host's first connection arrives after that. */
static void display_init(void)
{
    lcd_init(MADCTL_MV | MADCTL_BGR, false);
    lcd_set_rotation(1);
}

static void send_hello_ack(void)
{
    const uint16_t window = LINK_WINDOW_BYTES, max_payload = PROTO_MAX_PAYLOAD;
    const uint16_t fw = FW_VERSION, w = lcd_width(), h = lcd_height();
    const uint8_t p[11] = {
        PROTO_VERSION,
        (uint8_t)fw, (uint8_t)(fw >> 8),
        (uint8_t)w, (uint8_t)(w >> 8),
        (uint8_t)h, (uint8_t)(h >> 8),
        (uint8_t)window, (uint8_t)(window >> 8),
        (uint8_t)max_payload, (uint8_t)(max_payload >> 8),
    };
    link_send(MSG_HELLO_ACK, p, sizeof p);
}

static void on_frame(uint8_t type, const link_span_t *p)
{
    const uint16_t len = link_span_len(p);
    const uint32_t now = HAL_GetTick();

    if (type == MSG_STATUS) {
        /* The bridge only sends this while no client is connected: the session is over. */
        if (len == 1) {
            s_connected = false;
            ui_show_offline(link_span_u8(p, 0));
        }
        return;
    }
    if (type == MSG_HELLO) {
        if (len < 1 || link_span_u8(p, 0) != PROTO_VERSION) {
            link_log("hello: protocol version mismatch");
        }
        s_connected = true;
        s_last_rx_ms = now;
        blit_reset(); /* drop any half-finished blit */
        send_hello_ack(); /* before drawing, so the buffer starts filling while the host waits */
        display_init();   /* ~0.5 s; incoming data waits in the DMA buffer (window < buffer) */
        ui_show_connected();
        return;
    }
    if (!s_connected) {
        return; /* everything is ignored until HELLO; no CREDIT is sent either */
    }
    s_last_rx_ms = now;

    switch (type) {
    case MSG_BLIT_BEGIN:
        log_if(len == 8 ? blit_begin(link_span_u16(p, 0), link_span_u16(p, 2), link_span_u16(p, 4), link_span_u16(p, 6))
                        : blit_abort("blit: malformed BEGIN"));
        break;
    case MSG_BLIT_DATA:
        log_if(blit_data(p));
        break;
    case MSG_BLIT_END:
        log_if(blit_end());
        break;
    case MSG_PROGRESS:
        if (len == 9) {
            ui_set_progress(link_span_u32(p, 0), link_span_u32(p, 4), link_span_u8(p, 8) != 0, now);
        }
        break;
    case MSG_FILL:
        if (len == 10) {
            lcd_fill_rect((int16_t)link_span_u16(p, 0), (int16_t)link_span_u16(p, 2), (int16_t)link_span_u16(p, 4),
                          (int16_t)link_span_u16(p, 6), link_span_u16(p, 8));
        }
        break;
    case MSG_PING:
        break;
    default:
        link_log("unknown frame type");
        break;
    }
}

static void on_button(uint8_t page, uint8_t index, uint8_t event)
{
    const uint8_t p[3] = { page, index, event };
    link_send(MSG_BUTTON, p, sizeof p);
}

int main(void)
{
    board_init();
    lcd_bus_init();
    HAL_Delay(POWERUP_SETTLE_MS);
    display_init();
    touch_init();
    link_init();
    ui_init(on_button);
    ui_show_offline(UI_OFFLINE_LINK_LOST);
    link_log("boot");

    uint32_t last_blink = 0;
    for (;;) {
        link_poll(on_frame, FRAMES_PER_POLL);
        const uint32_t now = HAL_GetTick();

        if (s_connected && now - s_last_rx_ms > PING_TIMEOUT_MS) {
            s_connected = false;
            ui_show_offline(UI_OFFLINE_LINK_LOST);
        }
        if (s_connected) {
            link_credit_tick(now);
        }
        ui_tick(now);
        link_log_tick(now);

        if (now - last_blink >= (s_connected ? 100u : 500u)) {
            last_blink = now;
            board_led_toggle();
        }
    }
}
