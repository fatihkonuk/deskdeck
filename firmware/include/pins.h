/* All pin definitions live here. Wiring: docs/hardware.md */
#pragma once

#include "stm32f1xx.h"

/* Data bus: LCD_D0..D7 → PA0..PA7, straight through.
 * PA0..PA7 are the only pins in GPIOA->CRL, so CRL can be written directly. */
#define LCD_DATA_PORT GPIOA

/* All control lines are on GPIOB. */
#define LCD_CTRL_PORT GPIOB
#define LCD_RD_PIN    (1u << 7)
#define LCD_RS_PIN    (1u << 1)
#define LCD_RST_PIN   (1u << 9)
#define LCD_WR_PIN    (1u << 6)
#define LCD_CS_PIN    (1u << 0)

/* Touch: mapping A (found with tools/touch_diag.py). Shares the LCD lines.
 * Pin numbers (not masks); XM and YP have ADC channels. */
#define TOUCH_XP_PORT GPIOA /* LCD_D0 */
#define TOUCH_XP_PIN  0
#define TOUCH_XM_PORT GPIOB /* LCD_RS */
#define TOUCH_XM_PIN  1
#define TOUCH_XM_ADC  9
#define TOUCH_YP_PORT GPIOB /* LCD_CS */
#define TOUCH_YP_PIN  0
#define TOUCH_YP_ADC  8
#define TOUCH_YM_PORT GPIOA /* LCD_D1 */
#define TOUCH_YM_PIN  1

/* On-board LED, active low. */
#define LED_PORT      GPIOC
#define LED_PIN       (1u << 13)
