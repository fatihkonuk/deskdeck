/* Touch wiring diagnostic: finds which LCD pins the touch panel is connected to.
 * Each candidate pin is driven LOW in turn while the others are read as pulled-up inputs. The partner
 * connected through the panel layer (a few hundred Ω) reads LOW. The result is in g_touch_diag,
 * which tools/touch_diag.py reads over SWD. The LCD bus pins are restored afterwards. */
#pragma once

void touch_diag_run(void);
