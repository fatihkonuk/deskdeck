#include "board.h"

#include "pins.h"
#include "stm32f1xx_hal.h"

void SysTick_Handler(void)
{
    HAL_IncTick();
}

/* HSE 8 MHz × 9 = 72 MHz. If HSE does not start, fall back to HSI/2 × 16 = 64 MHz. */
static void clock_init(void)
{
    RCC_OscInitTypeDef osc = {
        .OscillatorType = RCC_OSCILLATORTYPE_HSE,
        .HSEState = RCC_HSE_ON,
        .HSEPredivValue = RCC_HSE_PREDIV_DIV1,
        .PLL = { .PLLState = RCC_PLL_ON, .PLLSource = RCC_PLLSOURCE_HSE, .PLLMUL = RCC_PLL_MUL9 },
    };
    if (HAL_RCC_OscConfig(&osc) != HAL_OK) {
        osc = (RCC_OscInitTypeDef){
            .OscillatorType = RCC_OSCILLATORTYPE_HSI,
            .HSIState = RCC_HSI_ON,
            .HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT,
            .PLL = { .PLLState = RCC_PLL_ON, .PLLSource = RCC_PLLSOURCE_HSI_DIV2, .PLLMUL = RCC_PLL_MUL16 },
        };
        HAL_RCC_OscConfig(&osc);
    }

    RCC_ClkInitTypeDef clk = {
        .ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2,
        .SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK,
        .AHBCLKDivider = RCC_SYSCLK_DIV1,
        .APB1CLKDivider = RCC_HCLK_DIV2,
        .APB2CLKDivider = RCC_HCLK_DIV1,
    };
    HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_2);
}

static void led_init(void)
{
    __HAL_RCC_GPIOC_CLK_ENABLE();
    GPIO_InitTypeDef gpio = {
        .Pin = LED_PIN,
        .Mode = GPIO_MODE_OUTPUT_PP,
        .Pull = GPIO_NOPULL,
        .Speed = GPIO_SPEED_FREQ_LOW,
    };
    HAL_GPIO_Init(LED_PORT, &gpio);
}

void board_init(void)
{
    HAL_Init();
    clock_init();
    led_init();

    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

void board_led_toggle(void)
{
    LED_PORT->ODR ^= LED_PIN;
}
