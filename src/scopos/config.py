# -*- coding: utf-8 -*-
"""Central layout & theme configuration for Scopos.

Everything here is cosmetic — spacing, sizing, which columns show, and colours.
There are two ways to change it:

1. Edit the defaults in this file, or
2. Drop a ``config.toml`` (or ``config.json``) into ``~/.scopos`` (honours
   ``$SCOPOS_HOME``) to override any of them without touching the source. Only
   the keys you list are overridden; the rest keep their defaults.

Values are read once at start-up, so restart ``scopos`` after changing them.

Example ``~/.scopos/config.toml``::

    card_min_width = 90
    table_cell_padding = 2
    grid_gutter = [1, 3]

    [column_widths]
    COMMAND = 30
    SESSION = 20

    [column_visible]
    "S.START" = false
    USER = false

    [colors]
    progress = "magenta"
    watch_user = "bright_blue"

(TOML needs Python 3.11+, or the ``tomli`` package on older Pythons; ``config.json``
always works.)
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import (Dict, List, Optional)

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
    "NO.": 5,
    "MEM/GB": 6,
    "RAM/GB": 6,
    "RUNTIME": 7,
    "SESSION": 10,
    "S.START": 17,
    "COMMAND": 30,
}

# Which built-in columns to show.  Set any to ``False`` to hide it (metadata
# columns are always shown).  Missing keys default to visible.
COLUMN_VISIBLE: Dict[str, bool] = {
    "PID": True,
    "USER": True,
    "NO.": True,
    "MEM/GB": True,
    "RAM/GB": True,
    "RUNTIME": True,
    "SESSION": True,
    "S.START": True,
    "COMMAND": True,
}

# --- colours / theme ------------------------------------------------------
# Colours are Rich/Textual names (e.g. "bright_green") or hex ("#5fd7ff").

# Per-user colours, assigned in order of first appearance and kept stable.
USER_PALETTE: List[str] = [
    "bright_green",
    "bright_yellow",
    "bright_magenta",
    "bright_red",
    "bright_cyan",
    "orange1",
    "spring_green2",
    "deep_pink2",
    "gold1",
    "dodger_blue1",
    "medium_purple1",
    "chartreuse2",
    "hot_pink",
]

# Colour reserved for the watched user (-u/--user) so they stand out.
FOCUS_USER_COLOR = "bright_blue"

# Progress bars.
PROGRESS_WIDTH = 14            # Width, in cells, of a rendered progress bar inside a table cell.
PROGRESS_COLOR = "bold white"       # default bar fill when a report doesn't set one
BAR_TRACK_COLOR = "grey35"     # the unfilled part of bars
PROGRESS_LABEL_COLOR = "dim"   # the percentage/label text shown next to bars

# Generic status colours (used for the GPU free-memory & host-RAM indicators).
COLOR_OK = "green"
COLOR_WARN = "yellow"
COLOR_CRIT = "red"

# GPU free-memory indicator, as a fraction of total memory free.
MEM_FREE_WARN = 0.5            # below this -> COLOR_WARN
MEM_FREE_CRIT = 0.15           # below this -> COLOR_CRIT

# Host RAM / swap meter (top-right), as a fraction used.
SYS_MEM_WARN = 0.6             # at/above this -> COLOR_WARN
SYS_MEM_CRIT = 0.85            # at/above this -> COLOR_CRIT

# Host RAM / swap meter bar sizing (it shrinks to fit a narrow terminal).
SYS_BAR_MAX = 26               # widest the meter bar gets on a roomy terminal
SYS_BAR_MIN = 4                # narrowest bar before trailing text is dropped
SYS_METER_MIN = 10             # smallest meter ever drawn (label + a tiny bar)

# GPU temperature.
TEMP_WARN_C = 80               # at/above this -> COLOR_CRIT
TEMP_COLOR = "cyan"            # normal temperature colour


# -- external overrides ----------------------------------------------------
def _config_path() -> Path:
    base = os.environ.get("SCOPOS_HOME")
    return (Path(base) if base else Path.home() / ".scopos")


def _read_external() -> Optional[dict]:
    """Load ``config.toml`` (preferred) or ``config.json`` from ~/.scopos."""
    base = _config_path()
    toml_path = base / "config.toml"
    if toml_path.exists():
        try:
            import tomllib as _toml  # Python 3.11+
        except ModuleNotFoundError:
            try:
                import tomli as _toml  # backport
            except ModuleNotFoundError:
                _toml = None
        if _toml is not None:
            try:
                with open(toml_path, "rb") as fh:
                    return _toml.load(fh)
            except Exception:
                return None
    json_path = base / "config.json"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None
    return None


def _apply_overrides(data: dict) -> None:
    g = globals()

    def take_scalar(name: str):
        key = name.lower()
        if key in data:
            g[name] = data[key]

    for name in (
        "CARD_MIN_WIDTH", "CARD_MAX_WIDTH", "TABLE_CELL_PADDING", "TABLE_MAX_HEIGHT",
        "WATCH_USER_COLOR", "PROGRESS_COLOR", "BAR_TRACK_COLOR",
        "COLOR_OK", "COLOR_WARN", "COLOR_CRIT",
        "MEM_FREE_WARN", "MEM_FREE_CRIT", "SYS_MEM_WARN", "SYS_MEM_CRIT",
        "SYS_BAR_MAX", "SYS_BAR_MIN", "SYS_METER_MIN",
        "TEMP_WARN_C", "TEMP_COLOR",
    ):
        take_scalar(name)

    for name in ("GRID_GUTTER", "GRID_PADDING", "CARD_PADDING"):
        val = data.get(name.lower())
        if isinstance(val, (list, tuple)) and len(val) == 2:
            g[name] = (val[0], val[1])

    if isinstance(data.get("user_palette"), list) and data["user_palette"]:
        g["USER_PALETTE"] = [str(c) for c in data["user_palette"]]

    if isinstance(data.get("column_widths"), dict):
        COLUMN_WIDTHS.update(data["column_widths"])
    if isinstance(data.get("column_visible"), dict):
        for key, vis in data["column_visible"].items():
            COLUMN_VISIBLE[key] = bool(vis)

    # Colours may be grouped under a [colors] table for convenience.
    colors = data.get("colors")
    if isinstance(colors, dict):
        alias = {
            "progress": "PROGRESS_COLOR", "bar_track": "BAR_TRACK_COLOR",
            "watch_user": "WATCH_USER_COLOR", "ok": "COLOR_OK",
            "warn": "COLOR_WARN", "crit": "COLOR_CRIT", "temp": "TEMP_COLOR",
        }
        for key, name in alias.items():
            if key in colors:
                g[name] = colors[key]
        if isinstance(colors.get("user_palette"), list) and colors["user_palette"]:
            g["USER_PALETTE"] = [str(c) for c in colors["user_palette"]]


def _load() -> None:
    data = _read_external()
    if isinstance(data, dict):
        try:
            _apply_overrides(data)
        except Exception:
            # A malformed config should never stop scopos from starting.
            pass


_load()
