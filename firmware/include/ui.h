/* What the firmware draws itself: button frames and press feedback, the progress bar and the
 * "not connected" screens. Cover art, text and button icons are blitted by the host.
 * Layout v1 (480×320, landscape). Must match docs/protocol.md and host/deskdeck/layout.py. */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#define UI_COVER_X      8
#define UI_COVER_Y      8
#define UI_COVER_SIZE   128

#define UI_PROGRESS_X   148
#define UI_PROGRESS_Y   124
#define UI_PROGRESS_W   324
#define UI_PROGRESS_H   6

#define UI_GRID_X       4
#define UI_GRID_Y       152
#define UI_GRID_COLS    4
#define UI_GRID_ROWS    2
#define UI_CELL_W       118
#define UI_CELL_H       82
#define UI_BUTTON_COUNT (UI_GRID_COLS * UI_GRID_ROWS)

typedef void (*ui_button_cb_t)(uint8_t page, uint8_t index, uint8_t event);

void ui_init(ui_button_cb_t on_button);
/* Screen shown while not connected. 0: link to the host lost (PING timeout),
 * other values are the bridge's STATUS values (BRIDGE_*). Does not redraw if the state is unchanged. */
#define UI_OFFLINE_LINK_LOST 0
void ui_show_offline(uint8_t state);
void ui_show_connected(void); /* black background, button frames, empty progress bar */
void ui_set_progress(uint32_t elapsed_ms, uint32_t duration_ms, bool playing, uint32_t now_ms);

/* Called often from the main loop: touch every 10 ms, progress bar every 100 ms. */
void ui_tick(uint32_t now_ms);
