/* Changes the mode of a single pin through CRL/CRH (much faster than HAL_GPIO_Init). */
#pragma once

#include "stm32f1xx.h"

/* 4-bit CRx field: CNF[1:0] MODE[1:0] */
#define GPIO_CR_ANALOG      0x0u
#define GPIO_CR_INPUT_FLOAT 0x4u
#define GPIO_CR_INPUT_PULL  0x8u /* ODR=1 pull-up, ODR=0 pull-down */
#define GPIO_CR_OUTPUT_PP   0x3u /* 50 MHz */

static inline void gpio_pin_mode(GPIO_TypeDef *port, uint32_t pin, uint32_t mode)
{
    volatile uint32_t *cr = pin < 8 ? &port->CRL : &port->CRH;
    uint32_t shift = (pin & 7u) * 4u;
    *cr = (*cr & ~(0xFu << shift)) | (mode << shift);
}
