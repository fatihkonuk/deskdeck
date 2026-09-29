#include "lcd.h"

#include <stdlib.h>

#include "lcd_bus.h"
#include "stm32f1xx_hal.h"

/* MIPI DCS commands */
#define DCS_SWRESET 0x01
#define DCS_SLPOUT  0x11
#define DCS_INVOFF  0x20
#define DCS_INVON   0x21
#define DCS_DISPON  0x29
#define DCS_CASET   0x2A
#define DCS_PASET   0x2B
#define DCS_RAMWR   0x2C
#define DCS_MADCTL  0x36
#define DCS_COLMOD  0x3A

static uint16_t s_width = LCD_NATIVE_W;
static uint16_t s_height = LCD_NATIVE_H;

static void cmd1(uint8_t cmd, uint8_t arg)
{
    lcd_cs_low();
    lcd_write_cmd(cmd);
    lcd_write_data(arg);
    lcd_cs_high();
}

static void cmd0(uint8_t cmd)
{
    lcd_cs_low();
    lcd_write_cmd(cmd);
    lcd_cs_high();
}

void lcd_init(uint8_t madctl, bool invert)
{
    lcd_bus_hw_reset();
    cmd0(DCS_SWRESET);
    HAL_Delay(150);
    cmd0(DCS_SLPOUT);
    HAL_Delay(150);
    cmd1(DCS_COLMOD, 0x55); /* 16 bits/pixel (RGB565) */
    lcd_set_madctl(madctl);
    cmd0(invert ? DCS_INVON : DCS_INVOFF);
    cmd0(DCS_DISPON);
    HAL_Delay(20);
}

void lcd_set_madctl(uint8_t madctl)
{
    cmd1(DCS_MADCTL, madctl);
    if (madctl & MADCTL_MV) {
        s_width = LCD_NATIVE_H;
        s_height = LCD_NATIVE_W;
    } else {
        s_width = LCD_NATIVE_W;
        s_height = LCD_NATIVE_H;
    }
}

void lcd_set_rotation(uint8_t rotation)
{
    static const uint8_t madctl[4] = {
        MADCTL_MX | MADCTL_BGR,
        MADCTL_MV | MADCTL_BGR,
        MADCTL_MY | MADCTL_BGR,
        MADCTL_MY | MADCTL_MX | MADCTL_MV | MADCTL_BGR,
    };
    lcd_set_madctl(madctl[rotation & 3]);
}

uint16_t lcd_width(void) { return s_width; }
uint16_t lcd_height(void) { return s_height; }

/* Opens the address window and prepares for writing with RAMWR. CS stays low. */
static void set_window(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1)
{
    lcd_cs_low();
    lcd_write_cmd(DCS_CASET);
    lcd_write_data(x0 >> 8);
    lcd_write_data(x0 & 0xFF);
    lcd_write_data(x1 >> 8);
    lcd_write_data(x1 & 0xFF);
    lcd_write_cmd(DCS_PASET);
    lcd_write_data(y0 >> 8);
    lcd_write_data(y0 & 0xFF);
    lcd_write_data(y1 >> 8);
    lcd_write_data(y1 & 0xFF);
    lcd_write_cmd(DCS_RAMWR);
}

void lcd_fill_rect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color)
{
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > s_width) { w = s_width - x; }
    if (y + h > s_height) { h = s_height - y; }
    if (w <= 0 || h <= 0) {
        return;
    }

    set_window(x, y, x + w - 1, y + h - 1);
    lcd_write_color_repeat(color, (uint32_t)w * h);
    lcd_cs_high();
}

void lcd_begin_write(int16_t x, int16_t y, int16_t w, int16_t h)
{
    set_window(x, y, x + w - 1, y + h - 1);
}

void lcd_end_write(void)
{
    lcd_cs_high();
}

void lcd_fill_screen(uint16_t color)
{
    lcd_fill_rect(0, 0, s_width, s_height, color);
}

void lcd_draw_pixel(int16_t x, int16_t y, uint16_t color)
{
    lcd_fill_rect(x, y, 1, 1, color);
}

void lcd_draw_line(int16_t x0, int16_t y0, int16_t x1, int16_t y1, uint16_t color)
{
    if (y0 == y1) {
        lcd_fill_rect(x0 < x1 ? x0 : x1, y0, abs(x1 - x0) + 1, 1, color);
        return;
    }
    if (x0 == x1) {
        lcd_fill_rect(x0, y0 < y1 ? y0 : y1, 1, abs(y1 - y0) + 1, color);
        return;
    }

    /* Bresenham */
    int16_t dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    int16_t dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    int16_t err = dx + dy;
    for (;;) {
        lcd_draw_pixel(x0, y0, color);
        if (x0 == x1 && y0 == y1) {
            break;
        }
        int16_t e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
}

void lcd_draw_rect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t color)
{
    lcd_fill_rect(x, y, w, 1, color);
    lcd_fill_rect(x, y + h - 1, w, 1, color);
    lcd_fill_rect(x, y, 1, h, color);
    lcd_fill_rect(x + w - 1, y, 1, h, color);
}
