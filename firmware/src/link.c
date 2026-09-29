#include "link.h"

#include <string.h>

#include "gpio_util.h"
#include "stm32f1xx_hal.h"

#define RX_MASK             (LINK_RX_BUF_SIZE - 1u)
#define CREDIT_BATCH_BYTES  2048u /* over Wi-Fi every CREDIT is its own TCP packet: few, but enough */
#define CREDIT_INTERVAL_MS  50u
/* Repeat even when unchanged, so a lost final CREDIT is made up for. 50 ms was tried (hoping for
 * faster retransmits): the more packets the ESP sent, the worse the latency got. */
#define CREDIT_REPEAT_MS    500u
/* LOG rate limit: every LOG busy-waits on the UART, so a burst of bad frames (e.g. an unknown type
 * from a newer host) must not stall frame handling. The excess is counted and reported once. */
#define LOG_WINDOW_MS       1000u
#define LOG_BURST           4u

_Static_assert((LINK_RX_BUF_SIZE & RX_MASK) == 0, "LINK_RX_BUF_SIZE must be a power of two");

static uint8_t s_rx[LINK_RX_BUF_SIZE];
static uint32_t s_rd;
static uint32_t s_reported;
static uint32_t s_last_report_ms;
static uint32_t s_log_window_ms;   /* start of the current LOG window */
static uint32_t s_log_sent;        /* LOGs sent in the current window */
static uint32_t s_log_suppressed;  /* LOGs dropped in the current window */

__attribute__((used)) volatile link_stats_t g_link_stats;

void link_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_AFIO_CLK_ENABLE();
    __HAL_RCC_USART1_CLK_ENABLE();
    __HAL_RCC_DMA1_CLK_ENABLE();

    /* PA9 TX: alternate function push-pull. PA10 RX: floating input; the ESP's GPIO15 must be LOW
     * at boot and a pull-up would break that (docs/troubleshooting.md). */
    gpio_pin_mode(GPIOA, 9, 0xBu);
    gpio_pin_mode(GPIOA, 10, GPIO_CR_INPUT_FLOAT);

    DMA1_Channel5->CCR = 0;
    DMA1_Channel5->CPAR = (uint32_t)&USART1->DR;
    DMA1_Channel5->CMAR = (uint32_t)s_rx;
    DMA1_Channel5->CNDTR = LINK_RX_BUF_SIZE;
    DMA1_Channel5->CCR = DMA_CCR_MINC | DMA_CCR_CIRC | DMA_CCR_PL_1 | DMA_CCR_EN;

    /* USART1 is on APB2 (same as HCLK). At 72 MHz BRR=78 → 923 077 baud (0.16%). */
    USART1->BRR = (SystemCoreClock + LINK_BAUD / 2) / LINK_BAUD;
    USART1->CR3 = USART_CR3_DMAR;
    USART1->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;
}

static uint32_t rx_write_pos(void)
{
    return (LINK_RX_BUF_SIZE - DMA1_Channel5->CNDTR) & RX_MASK;
}

static uint32_t rx_available(void)
{
    return (rx_write_pos() - s_rd) & RX_MASK;
}

static uint8_t rx_at(uint32_t off)
{
    return s_rx[(s_rd + off) & RX_MASK];
}

static void rx_consume(uint32_t n)
{
    s_rd = (s_rd + n) & RX_MASK;
    g_link_stats.consumed += n;
}

static link_span_t rx_span(uint32_t off, uint32_t len)
{
    uint32_t start = (s_rd + off) & RX_MASK;
    uint32_t first = LINK_RX_BUF_SIZE - start;
    link_span_t s = { .a = &s_rx[start], .b = s_rx };
    s.a_len = (uint16_t)(len < first ? len : first);
    s.b_len = (uint16_t)(len - s.a_len);
    return s;
}

void link_poll(link_handler_t handler, uint32_t max_frames)
{
    while (max_frames--) {
        uint32_t avail = rx_available();
        while (avail >= 2 && !(rx_at(0) == PROTO_SYNC0 && rx_at(1) == PROTO_SYNC1)) {
            rx_consume(1);
            avail--;
            g_link_stats.skipped++;
        }
        if (avail < PROTO_HEADER_LEN) {
            return;
        }

        const uint8_t type = rx_at(2);
        const uint16_t len = (uint16_t)(rx_at(3) | (rx_at(4) << 8));
        if (len > PROTO_MAX_PAYLOAD) {
            rx_consume(1);
            g_link_stats.bad_len++;
            continue;
        }
        const uint32_t total = PROTO_HEADER_LEN + len + PROTO_CRC_LEN;
        if (avail < total) {
            return;
        }

        link_span_t covered = rx_span(2, 3u + len);
        uint16_t crc = crc16_ccitt(0xFFFF, covered.a, covered.a_len);
        crc = crc16_ccitt(crc, covered.b, covered.b_len);
        const uint16_t got = (uint16_t)(rx_at(PROTO_HEADER_LEN + len) | (rx_at(PROTO_HEADER_LEN + len + 1) << 8));
        if (crc != got) {
            rx_consume(1);
            g_link_stats.crc_errors++;
            continue;
        }

        link_span_t payload = rx_span(PROTO_HEADER_LEN, len);
        handler(type, &payload);
        rx_consume(total);
        g_link_stats.frames++;
        if (type == MSG_HELLO) {
            g_link_stats.consumed = 0;
            s_reported = 0;
        }
    }
}

void link_credit_tick(uint32_t now_ms)
{
    const uint32_t consumed = g_link_stats.consumed;
    const uint32_t since_ms = now_ms - s_last_report_ms;
    if (consumed == s_reported ? since_ms < CREDIT_REPEAT_MS
                               : consumed - s_reported < CREDIT_BATCH_BYTES && since_ms < CREDIT_INTERVAL_MS) {
        return;
    }
    link_send(MSG_CREDIT, &consumed, sizeof consumed); /* Cortex-M3 is little-endian */
    s_reported = consumed;
    s_last_report_ms = now_ms;
}

static void tx_bytes(const uint8_t *p, uint32_t n)
{
    while (n--) {
        while (!(USART1->SR & USART_SR_TXE)) {
        }
        USART1->DR = *p++;
    }
}

void link_send(uint8_t type, const void *payload, uint16_t len)
{
    const uint8_t header[PROTO_HEADER_LEN] = { PROTO_SYNC0, PROTO_SYNC1, type, (uint8_t)len, (uint8_t)(len >> 8) };
    uint16_t crc = crc16_ccitt(0xFFFF, &header[2], 3);
    crc = crc16_ccitt(crc, payload, len);
    const uint8_t trailer[PROTO_CRC_LEN] = { (uint8_t)crc, (uint8_t)(crc >> 8) };

    tx_bytes(header, sizeof header);
    tx_bytes(payload, len);
    tx_bytes(trailer, sizeof trailer);
}

/* Writes v in decimal to out (at least 10 bytes); returns the number of characters. */
static uint16_t fmt_u32(char *out, uint32_t v)
{
    char tmp[10];
    uint16_t n = 0;
    do {
        tmp[n++] = (char)('0' + v % 10u);
        v /= 10u;
    } while (v);
    for (uint16_t i = 0; i < n; i++) {
        out[i] = tmp[n - 1u - i];
    }
    return n;
}

void link_log_tick(uint32_t now_ms)
{
    if (now_ms - s_log_window_ms < LOG_WINDOW_MS) {
        return;
    }
    if (s_log_suppressed) {
        static const char prefix[] = "log: ", suffix[] = " messages suppressed";
        char msg[sizeof prefix - 1u + 10u + sizeof suffix - 1u];
        uint16_t n = sizeof prefix - 1u;
        memcpy(msg, prefix, n);
        n += fmt_u32(msg + n, s_log_suppressed);
        memcpy(msg + n, suffix, sizeof suffix - 1u);
        n += sizeof suffix - 1u;
        link_send(MSG_LOG, msg, n);
    }
    s_log_window_ms = now_ms;
    s_log_sent = 0;
    s_log_suppressed = 0;
}

void link_log(const char *text)
{
    link_log_tick(HAL_GetTick());
    if (s_log_sent >= LOG_BURST) {
        s_log_suppressed++;
        return;
    }
    s_log_sent++;
    link_send(MSG_LOG, text, (uint16_t)strlen(text));
}
