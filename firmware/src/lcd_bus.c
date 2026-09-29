#include "lcd_bus.h"

#include "stm32f1xx_hal.h"

/* CRL for PA0..PA7: MODE=11 (50 MHz output), CNF=00 (push-pull). PA0..PA7 are the only pins in CRL. */
#define DATA_CRL_OUTPUT 0x33333333u

void lcd_bus_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();

    /* RD is driven high here and never touched again (see lcd_bus.h). */
    LCD_CTRL_HIGH(LCD_CS_PIN | LCD_RD_PIN | LCD_WR_PIN | LCD_RS_PIN | LCD_RST_PIN);

    GPIO_InitTypeDef gpio = {
        .Pin = LCD_CS_PIN | LCD_RD_PIN | LCD_WR_PIN | LCD_RS_PIN | LCD_RST_PIN,
        .Mode = GPIO_MODE_OUTPUT_PP,
        .Pull = GPIO_NOPULL,
        .Speed = GPIO_SPEED_FREQ_HIGH,
    };
    HAL_GPIO_Init(LCD_CTRL_PORT, &gpio);

    LCD_DATA_PORT->CRL = DATA_CRL_OUTPUT;
}

void lcd_bus_hw_reset(void)
{
    LCD_CTRL_HIGH(LCD_RST_PIN);
    HAL_Delay(5);
    LCD_CTRL_LOW(LCD_RST_PIN);
    HAL_Delay(20);
    LCD_CTRL_HIGH(LCD_RST_PIN);
    HAL_Delay(150); /* ILI948x/ST7796: 120 ms after reset */
}

void lcd_write_cmd(uint8_t cmd)
{
    LCD_CTRL_LOW(LCD_RS_PIN);
    lcd_write8(cmd);
    LCD_CTRL_HIGH(LCD_RS_PIN);
}

void lcd_write_data(uint8_t data)
{
    lcd_write8(data);
}

void lcd_write_color_repeat(uint16_t color, uint32_t count)
{
    const uint32_t hi = LCD_BSRR(color >> 8);
    const uint32_t lo = LCD_BSRR(color & 0xFF);

    if (hi == lo) {
        /* If the high and low bytes match (black, white...) the bus stays put and only WR is strobed. */
        LCD_DATA_PORT->BSRR = hi;
        while (count--) {
            lcd_wr_strobe();
            lcd_wr_strobe();
        }
        return;
    }
    while (count--) {
        LCD_DATA_PORT->BSRR = hi;
        lcd_wr_strobe();
        LCD_DATA_PORT->BSRR = lo;
        lcd_wr_strobe();
    }
}

void lcd_write_bytes(const uint8_t *p, uint32_t n)
{
    while (n--) {
        lcd_write8(*p++);
    }
}
