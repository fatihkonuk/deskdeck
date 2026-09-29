"""Screen layout v1 (480×320, landscape). Must match firmware/include/ui.h and docs/protocol.md."""

WIDTH, HEIGHT = 480, 320

COVER = (8, 8, 128, 128)
TEXT = (148, 8, 324, 112)
# Lines inside TEXT: title (up to two lines), then artist and album
TITLE_LINES = ((148, 8, 324, 30), (148, 38, 324, 30))
ARTIST_LINE = (148, 70, 324, 26)
ALBUM_LINE = (148, 96, 324, 22)
PROGRESS_BAR = (148, 124, 324, 6)  # drawn by the MCU
TIME_TEXT = (148, 134, 324, 14)
TIME_TEXT_BLIT = (148, 134, 160, 14)  # only this part is sent every second
PAGE_DOTS = (392, 134, 80, 14)  # right end of TIME_TEXT: page indicator (right-aligned dots)

GRID_X, GRID_Y = 4, 152
CELL_W, CELL_H = 118, 82
GRID_COLS, GRID_ROWS = 4, 2
BUTTON_COUNT = GRID_COLS * GRID_ROWS
ICON_INSET = 6


def icon_rect(index: int) -> tuple[int, int, int, int]:
    """Area of a button icon (x, y, w, h); index is row-major 0..7."""
    col, row = index % GRID_COLS, index // GRID_COLS
    return (GRID_X + col * CELL_W + ICON_INSET, GRID_Y + row * CELL_H + ICON_INSET,
            CELL_W - 2 * ICON_INSET, CELL_H - 2 * ICON_INSET)
