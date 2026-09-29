#include "touch_diag.h"

#include "gpio_util.h"
#include "lcd_bus.h"
#include "stm32f1xx_hal.h"

#define DIAG_MAGIC 0x47414454u /* "TDAG" */

typedef struct {
    GPIO_TypeDef *port;
    uint8_t pin;
    uint8_t id; /* high nibble: port (0=A, 1=B), low nibble: pin number */
} cand_t;

/* Mapping A: XP=PA0 XM=PB1 YP=PB0 YM=PA1. Mapping B: XP=PA6 XM=PB1 YP=PB6 YM=PA7.
 * RD (PB7) is not a candidate: it must stay high (see lcd_bus.h). */
static const cand_t k_cands[] = {
    { GPIOA, 0, 0x00 }, { GPIOA, 1, 0x01 }, { GPIOA, 6, 0x06 }, { GPIOA, 7, 0x07 },
    { GPIOB, 0, 0x10 }, { GPIOB, 1, 0x11 }, { GPIOB, 6, 0x16 },
};
#define CAND_COUNT (sizeof k_cands / sizeof k_cands[0])

/* Layout must match tools/touch_diag.py. */
typedef struct {
    uint32_t magic;
    uint32_t runs;
    uint8_t count;
    uint8_t ids[CAND_COUNT];
    uint8_t low_mask[CAND_COUNT]; /* [i]: bit mask of candidates reading LOW while i is driven LOW */
} touch_diag_t;

__attribute__((used)) volatile touch_diag_t g_touch_diag;

void touch_diag_run(void)
{
    /* CS and WR are never LOW at the same time: while one is driven the other is pulled up, so nothing is written to the LCD. */
    for (uint32_t i = 0; i < CAND_COUNT; i++) {
        for (uint32_t j = 0; j < CAND_COUNT; j++) {
            k_cands[j].port->BSRR = 1u << k_cands[j].pin; /* ODR=1 → pull-up */
            gpio_pin_mode(k_cands[j].port, k_cands[j].pin, GPIO_CR_INPUT_PULL);
        }
        k_cands[i].port->BRR = 1u << k_cands[i].pin;
        gpio_pin_mode(k_cands[i].port, k_cands[i].pin, GPIO_CR_OUTPUT_PP);
        HAL_Delay(2);

        uint8_t mask = 0;
        for (uint32_t j = 0; j < CAND_COUNT; j++) {
            if (j != i && !(k_cands[j].port->IDR & (1u << k_cands[j].pin))) {
                mask |= (uint8_t)(1u << j);
            }
        }
        g_touch_diag.low_mask[i] = mask;
    }

    lcd_bus_init(); /* data bus output, control lines output and high */

    g_touch_diag.count = CAND_COUNT;
    for (uint32_t i = 0; i < CAND_COUNT; i++) {
        g_touch_diag.ids[i] = k_cands[i].id;
    }
    g_touch_diag.magic = DIAG_MAGIC;
    g_touch_diag.runs++;
}
