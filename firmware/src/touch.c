#include "touch.h"

#include <stdlib.h>

#include "gpio_util.h"
#include "lcd_bus.h"
#include "pins.h"
#include "stm32f1xx_hal.h"

#define SAMPLES        8
#define SETTLE_US      20
#define Z_MIN          300  /* pressure threshold, 12 bit; tune with tools/touch_ctl.py */
#define MAX_SPREAD     60   /* reading is invalid if the middle samples spread more than this */
#define MIN_CAL_SPAN   500  /* minimum raw span per axis during calibration */

/* 4-target calibration with a stylus tip. The panel's axes are swapped relative to the display.
 * Your panel will differ: recalibrate with `tools/touch_ctl.py --cal` (docs/hardware.md). */
const touch_cal_t touch_cal_default = {
    .swap_xy = 1,
    .x_a = 30, .x_b = 449, .y_a = 30, .y_b = 289,
    .raw_x_a = 517, .raw_x_b = 3428, .raw_y_a = 798, .raw_y_b = 3317,
};

static void delay_us(uint32_t us)
{
    uint32_t start = DWT->CYCCNT, cycles = us * (SystemCoreClock / 1000000u);
    while (DWT->CYCCNT - start < cycles) {
    }
}

void touch_init(void)
{
    __HAL_RCC_ADC_CONFIG(RCC_ADCPCLK2_DIV6); /* 72/6 = 12 MHz (max 14) */
    __HAL_RCC_ADC1_CLK_ENABLE();

    ADC1->CR1 = 0;
    ADC1->CR2 = ADC_CR2_ADON;
    delay_us(10);
    ADC1->CR2 |= ADC_CR2_RSTCAL;
    while (ADC1->CR2 & ADC_CR2_RSTCAL) {
    }
    ADC1->CR2 |= ADC_CR2_CAL;
    while (ADC1->CR2 & ADC_CR2_CAL) {
    }
    /* Software trigger (EXTSEL=111). 71.5-cycle sampling: source impedance is a few kΩ. */
    ADC1->CR2 |= ADC_CR2_EXTSEL | ADC_CR2_EXTTRIG;
    ADC1->SMPR2 = (6u << (TOUCH_YP_ADC * 3)) | (6u << (TOUCH_XM_ADC * 3));
    ADC1->SQR1 = 0; /* single conversion */
}

static uint16_t adc_read(uint32_t channel)
{
    ADC1->SQR3 = channel;
    ADC1->SR = 0;
    ADC1->CR2 |= ADC_CR2_SWSTART;
    while (!(ADC1->SR & ADC_SR_EOC)) {
    }
    return (uint16_t)ADC1->DR;
}

/* Read SAMPLES times, sort, average the middle half. -1 if the spread is too large. */
static int32_t adc_filtered(uint32_t channel)
{
    uint16_t v[SAMPLES];
    for (int i = 0; i < SAMPLES; i++) {
        uint16_t x = adc_read(channel);
        int j = i;
        for (; j > 0 && v[j - 1] > x; j--) {
            v[j] = v[j - 1];
        }
        v[j] = x;
    }
    const int lo = SAMPLES / 4, hi = SAMPLES - SAMPLES / 4 - 1;
    if (v[hi] - v[lo] > MAX_SPREAD) {
        return -1;
    }
    int32_t sum = 0;
    for (int i = lo; i <= hi; i++) {
        sum += v[i];
    }
    return sum / (hi - lo + 1);
}

static void drive(GPIO_TypeDef *port, uint32_t pin, bool high)
{
    if (high) {
        port->BSRR = 1u << pin;
    } else {
        port->BRR = 1u << pin;
    }
    gpio_pin_mode(port, pin, GPIO_CR_OUTPUT_PP);
}

void touch_read_raw(touch_raw_t *out)
{
    /* Pressure: XP LOW, YM HIGH; voltage at XM and YP. With no touch XM≈0, YP≈4095. */
    drive(TOUCH_XP_PORT, TOUCH_XP_PIN, false);
    drive(TOUCH_YM_PORT, TOUCH_YM_PIN, true);
    gpio_pin_mode(TOUCH_XM_PORT, TOUCH_XM_PIN, GPIO_CR_ANALOG);
    gpio_pin_mode(TOUCH_YP_PORT, TOUCH_YP_PIN, GPIO_CR_ANALOG);
    delay_us(SETTLE_US);
    int32_t z1 = adc_filtered(TOUCH_XM_ADC);
    int32_t z2 = adc_filtered(TOUCH_YP_ADC);

    /* X: drive the X layer (XP HIGH, XM LOW), read at YP. */
    drive(TOUCH_XP_PORT, TOUCH_XP_PIN, true);
    drive(TOUCH_XM_PORT, TOUCH_XM_PIN, false);
    gpio_pin_mode(TOUCH_YM_PORT, TOUCH_YM_PIN, GPIO_CR_INPUT_FLOAT);
    gpio_pin_mode(TOUCH_YP_PORT, TOUCH_YP_PIN, GPIO_CR_ANALOG);
    delay_us(SETTLE_US);
    int32_t x = adc_filtered(TOUCH_YP_ADC);

    /* Y: drive the Y layer (YP HIGH, YM LOW), read at XM. */
    drive(TOUCH_YP_PORT, TOUCH_YP_PIN, true);
    drive(TOUCH_YM_PORT, TOUCH_YM_PIN, false);
    gpio_pin_mode(TOUCH_XP_PORT, TOUCH_XP_PIN, GPIO_CR_INPUT_FLOAT);
    gpio_pin_mode(TOUCH_XM_PORT, TOUCH_XM_PIN, GPIO_CR_ANALOG);
    delay_us(SETTLE_US);
    int32_t y = adc_filtered(TOUCH_XM_ADC);

    lcd_bus_init(); /* data bus output, control lines output and high */

    int32_t z = (z1 < 0 || z2 < 0 || x < 0 || y < 0) ? 0 : 4095 - (z2 - z1);
    out->x = x < 0 ? 0 : (uint16_t)x;
    out->y = y < 0 ? 0 : (uint16_t)y;
    out->z = z < 0 ? 0 : z > 4095 ? 4095 : (uint16_t)z;
}

bool touch_pressed(const touch_raw_t *r)
{
    return r->z >= Z_MIN;
}

bool touch_calibrate(const touch_raw_t p[4], int16_t w, int16_t h, int16_t inset, touch_cal_t *out)
{
    /* Which raw axis changes more from left to right (p0,p3 → p1,p2)? */
    int32_t dx_by_x = abs((p[1].x + p[2].x) - (p[0].x + p[3].x));
    int32_t dx_by_y = abs((p[1].y + p[2].y) - (p[0].y + p[3].y));
    out->swap_xy = dx_by_y > dx_by_x;

    if (out->swap_xy) {
        out->raw_x_a = (p[0].y + p[3].y) / 2;
        out->raw_x_b = (p[1].y + p[2].y) / 2;
        out->raw_y_a = (p[0].x + p[1].x) / 2;
        out->raw_y_b = (p[3].x + p[2].x) / 2;
    } else {
        out->raw_x_a = (p[0].x + p[3].x) / 2;
        out->raw_x_b = (p[1].x + p[2].x) / 2;
        out->raw_y_a = (p[0].y + p[1].y) / 2;
        out->raw_y_b = (p[3].y + p[2].y) / 2;
    }
    out->x_a = inset;
    out->x_b = w - 1 - inset;
    out->y_a = inset;
    out->y_b = h - 1 - inset;

    return abs(out->raw_x_b - out->raw_x_a) >= MIN_CAL_SPAN && abs(out->raw_y_b - out->raw_y_a) >= MIN_CAL_SPAN;
}

static int16_t lerp_clamp(int32_t r, int32_t ra, int32_t rb, int32_t sa, int32_t sb, int16_t max)
{
    int32_t s = sa + (r - ra) * (sb - sa) / (rb - ra);
    return (int16_t)(s < 0 ? 0 : s > max ? max : s);
}

void touch_map(const touch_cal_t *cal, const touch_raw_t *r, int16_t w, int16_t h, int16_t *sx, int16_t *sy)
{
    int32_t rx = cal->swap_xy ? r->y : r->x;
    int32_t ry = cal->swap_xy ? r->x : r->y;
    *sx = lerp_clamp(rx, cal->raw_x_a, cal->raw_x_b, cal->x_a, cal->x_b, w - 1);
    *sy = lerp_clamp(ry, cal->raw_y_a, cal->raw_y_b, cal->y_a, cal->y_b, h - 1);
}
