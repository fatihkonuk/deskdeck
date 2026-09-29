/* BLIT_BEGIN/DATA/END: streams an RGB565 rectangle from the host straight to the display, unbuffered.
 * Other drawing or a touch read may happen between chunks, so every chunk reopens the address
 * window at the next pixel: first the rest of the current row if it stopped mid-row, then the
 * remainder of the rectangle. */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "link.h"

/* Return a short description on error (for LOG), NULL on success. An error is reported once per blit:
 * the remaining DATA and the END of a rejected or broken blit are dropped without another message. */
const char *blit_begin(uint16_t x, uint16_t y, uint16_t w, uint16_t h);
const char *blit_data(const link_span_t *data);
const char *blit_end(void);
/* Marks the current blit as failed with `reason` (e.g. a malformed BEGIN frame) and returns it, so the
 * frames that follow are handled like those of a rejected blit. */
const char *blit_abort(const char *reason);
/* Forgets any blit in progress without reporting it (new session). */
void blit_reset(void);
