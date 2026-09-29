/* Board-level init: HAL, 72 MHz clock, on-board LED, DWT cycle counter. */
#pragma once

void board_init(void);
void board_led_toggle(void);
