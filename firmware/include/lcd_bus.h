/* 8-bit parallel LCD bus (8080 interface, write-only). Lowest, chip-independent layer.
 *
 * The shield has two 74LVC245A: one-way level shifters, so the shield is write-only.
 * RD is driven high at init and never lowered again. That way the shield never drives the data
 * lines towards the STM32 under any circumstances; PA0..PA7 are not 5V tolerant. See docs/hardware.md */
#pragma once

#include <stdint.h>

#include "pins.h"

/* BSRR value that puts a byte on PA0..PA7 in a single write: the low half sets, the high half
 * resets all of PA0..PA7. When both hit the same pin, set wins (RM0008 §9.2.5). */
#define LCD_BSRR(b) (0x00FF0000u | (uint8_t)(b))

#define LCD_CTRL_HIGH(pin) (LCD_CTRL_PORT->BSRR = (pin))
#define LCD_CTRL_LOW(pin)  (LCD_CTRL_PORT->BRR = (pin))

/* -Os turned these into calls (~26 cycles per byte); hot path, force inlining. */
#define LCD_HOT static inline __attribute__((always_inline))

/* ILI948x/ST7796: twrl, twrh ≥ 15 ns, twc ≥ 66 ns. One cycle at 72 MHz ≈ 14 ns. */
LCD_HOT void lcd_wr_strobe(void)
{
    LCD_CTRL_LOW(LCD_WR_PIN);
    __NOP();
    LCD_CTRL_HIGH(LCD_WR_PIN);
    __NOP();
}

LCD_HOT void lcd_write8(uint8_t b)
{
    LCD_DATA_PORT->BSRR = LCD_BSRR(b);
    lcd_wr_strobe();
}

LCD_HOT void lcd_cs_low(void)  { LCD_CTRL_LOW(LCD_CS_PIN); }
LCD_HOT void lcd_cs_high(void) { LCD_CTRL_HIGH(LCD_CS_PIN); }

void lcd_bus_init(void);
void lcd_bus_hw_reset(void);

/* These do not manage CS; the caller wraps them with lcd_cs_low/high. */
void lcd_write_cmd(uint8_t cmd);
void lcd_write_data(uint8_t data);

/* Writes the same RGB565 color count times (big-endian: high byte first). */
void lcd_write_color_repeat(uint16_t color, uint32_t count);

/* Writes bytes as-is as data (blit). */
void lcd_write_bytes(const uint8_t *p, uint32_t n);
