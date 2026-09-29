/* 4-wire resistive touch. The pins are shared with the LCD lines (pins.h). During a read they
 * temporarily become analog/input; afterwards the LCD bus is restored.
 * WR and RD stay high throughout, so nothing is written to the LCD. The DWT cycle counter must be enabled. */
#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Raw 12-bit ADC values. x: along the X layer, y: along the Y layer, z: pressure (0 = no touch). */
typedef struct {
    uint16_t x, y, z;
} touch_raw_t;

/* Calibration: raw values at screen points a and b (a is left/top, b is right/bottom).
 * swap_xy: screen x comes from raw y. */
typedef struct {
    uint8_t swap_xy;
    int16_t x_a, x_b, y_a, y_b;             /* screen coordinates */
    int16_t raw_x_a, raw_x_b, raw_y_a, raw_y_b;
} touch_cal_t;

/* Measured calibration for landscape (rotation 1, 480×320). */
extern const touch_cal_t touch_cal_default;

void touch_init(void);
void touch_read_raw(touch_raw_t *out);
bool touch_pressed(const touch_raw_t *r);

/* pts: raw values read at screen points (inset, inset), (w-1-inset, inset), (w-1-inset, h-1-inset),
 * (inset, h-1-inset). Returns false if the axes cannot be told apart. */
bool touch_calibrate(const touch_raw_t pts[4], int16_t w, int16_t h, int16_t inset, touch_cal_t *out);

/* Raw → screen coordinates, clamped to the screen. */
void touch_map(const touch_cal_t *cal, const touch_raw_t *r, int16_t w, int16_t h, int16_t *sx, int16_t *sy);
