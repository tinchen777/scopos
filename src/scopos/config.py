# -*- coding: utf-8 -*-
"""Central layout configuration for Scopos.

Everything in this file is purely cosmetic — spacing, sizing and column
widths.  Tweak the values here to tune the layout in one place; nothing here
changes *behaviour*, only how things look.

(Values are read once at start-up, so restart ``scopos`` after editing.)
"""

from __future__ import annotations
from typing import (Dict, Optional)

# --- cards & grid ---------------------------------------------------------

# Roughly the narrowest a GPU card stays readable.  The grid fits as many
# columns of cards as the terminal width allows at this minimum; lower it to
# pack more (narrower) cards per row, raise it for fewer (wider) cards.
CARD_MIN_WIDTH = 100

# Optional hard cap on a card's width (in cells).  ``None`` lets cards stretch
# to fill their grid column; set a number to keep them from getting too wide.
CARD_MAX_WIDTH: Optional[int] = None

# Gap between cards: (vertical_rows, horizontal_cells).
GRID_GUTTER = (1, 2)

# Padding inside the whole grid: (vertical, horizontal).
GRID_PADDING = (1, 2)

# Padding inside each card: (vertical, horizontal).
CARD_PADDING = (0, 1)

# --- process table --------------------------------------------------------

# Horizontal space on each side of a table cell — i.e. the gap between columns.
TABLE_CELL_PADDING = 1

# How tall a card's table may get (in rows) before it scrolls internally.
TABLE_MAX_HEIGHT = 20

# Per-column width caps, in cells.  A value of ``None`` (or a column missing
# from this map) auto-sizes to its content.  Columns wider than their cap are
# clipped in the cell with an ellipsis; hover to see the full value.
#
# NOTE: user-reported metadata columns (zen mode) are intentionally always
# auto-width so progress bars and custom fields show in full.
COLUMN_WIDTHS: Dict[str, Optional[int]] = {
    "PID": 7,
    "USER": 12,
    "NO.": 7,
    "MEM/GB": 8,
    "RAM/GB": 8,
    "RUNTIME": 9,
    "SESSION": 16,
    "S.START": 18,
    "COMMAND": 40,
}
