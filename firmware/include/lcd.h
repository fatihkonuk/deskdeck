/* Driver for MIPI DCS compatible 320×480 controllers (ILI9486/9488, ST7796, R61581, HX8357...).
 * Only commands common to all of them are used; no chip-specific power or gamma setup. */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#define LCD_NATIVE_W 320
#define LCD_NATIVE_H 480

/* MADCTL (0x36) bits */
#define MADCTL_MY  0x80
#define MADCTL_MX  0x40
#define MADCTL_MV  0x20 /* swap rows/columns: landscape */
#define MADCTL_ML  0x10
#define MADCTL_BGR 0x08
#define MADCTL_MH  0x04

#define RGB565(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))

void lcd_init(uint8_t madctl, bool invert);
void lcd_set_madctl(uint8_t madctl);

/* 0: portrait, 1: landscape (default), 2: portrait flipped, 3: landscape flipped.
 * Each step rotates by 90°. */
void lcd_set_rotation(uint8_t rotation);

uint16_t lcd_width(void);
uint16_t lcd_height(void);

/* Raw pixel stream: opens the window, then up to w*h*2 bytes are written with lcd_write_bytes and
 * the stream is closed with lcd_end_write. The rectangle must be on screen (no clipping). */
void lcd_begin_write(int16_t x, int16_t y, int16_t w, int16_t h);
void lcd_end_write(void);

/* Anything outside the screen is clipped. */
void lcd_fill_rect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color);
void lcd_fill_screen(uint16_t color);
void lcd_draw_pixel(int16_t x, int16_t y, uint16_t color);
void lcd_draw_line(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint16_t color);
void lcd_draw_rect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color);
