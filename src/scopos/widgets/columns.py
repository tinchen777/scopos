# -*- coding: utf-8 -*-
"""Column definitions and cell rendering shared by every process table.

This is the low-level "what a row looks like" layer: the column registry, the
progress-bar renderer and small text helpers. It has no widgets of its own
(beyond producing Rich ``Text``), so both :mod:`scopos.widgets.proc_table` and
the cards build on it without any circular dependency.
"""

from __future__ import annotations
from rich.text import Text
from typing import (Any, Callable, Dict, List, Optional, Tuple)

from .. import config
from ..metadata.utils import is_progress
from ..monitor import (ProcInfo, fmt_duration)
from ._utils import fmt_gb

# Width of the leading checkbox column shown while batch-selecting in kill mode.
CHECK_WIDTH = 3

SPINNER = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]
WAITING = " waiting..."


# -- progress rendering ----------------------------------------------------
def progress_text(data: Dict[str, Any]) -> str:
    """A plain-text summary of a progress field (for tooltips / clipboard)."""
    shown = str(data.get("label"))
    eta = data.get("eta")
    if eta is not None:
        shown += " [DONE]" if eta <= 0 else f" [~{fmt_duration(eta)}]"
    return shown


def render_progress(data: Dict[str, Any], frame: int = 0, width: int = config.PROGRESS_WIDTH) -> Text:
    """Render a progress field (see :func:`scopos.metadata.make_progress`).

    A determinate bar fills proportionally to its fraction; an indeterminate bar
    (``frac is None``) shows a spinner that advances by ``frame`` so the TUI can
    animate it between refreshes.
    """
    frac = data.get("frac")
    label = data.get("label")
    color = data.get("color") or config.PROGRESS_COLOR
    track = config.BAR_TRACK_COLOR
    width = int(max(1, width))
    bar = Text(no_wrap=True, overflow="crop")
    if frac is None:
        animate = Text(SPINNER[frame % len(SPINNER)], style=color)
        if width - len(WAITING) - 1 >= 0:
            animate.append(WAITING, style=color)
        animate.align("center", width + 3)
        bar.append(animate)
    else:
        bar.append("▕", style="grey50")
        if 0.0 <= frac <= 1.0:
            filled = int(round(frac * width))
            bar.append("■" * filled, style=color)
            bar.append(">" * (width - filled), style=track)
        else:
            bar.append("-" * width, style=track)
        bar.append("▏", style="grey50")
    bar.append(str(label), style=config.PROGRESS_LABEL_COLOR)
    eta = data.get("eta")
    if eta is not None:
        bar.append(" [")
        bar.append("DONE" if eta <= 0 else f"~{fmt_duration(eta)}",
                   style=config.COLOR_OK if eta <= 0 else "yellow")
        bar.append("]")
    return bar


# -- text helpers ----------------------------------------------------------
def clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def fit_cell(value: Any, width: Optional[int]) -> Any:
    """Clip a cell's text to ``width`` with an ellipsis; ``None`` = no clip.

    Only what's drawn is shortened — the hover tooltip recomputes the full value
    from the process, so nothing is lost.
    """
    if width is None:
        return value
    text = value if isinstance(value, Text) else Text(str(value))
    if len(text.plain) <= width:
        return value
    clipped = text.copy()
    clipped.truncate(width, overflow="ellipsis")
    return clipped


def _meta_sort_value(value: Any):
    """A sort key for a metadata cell that never compares across types."""
    if is_progress(value):
        v = value.get("frac")
        return (0, -1.0 if v is None else float(v))
    if value is None:
        return (2, "")
    try:
        return (0, float(value))
    except (TypeError, ValueError):
        return (1, str(value).lower())


# -- columns ---------------------------------------------------------------
class Column:
    """One table column: how to label, sort and render it.

    ``render(card, proc)`` returns a cell value (``str`` or Rich ``Text``).
    ``meta_key`` marks columns sourced from a process's reported metadata, so the
    table can find the animated progress cells. ``width`` caps the cell width
    (the full value is still stored, so hovering shows it); ``None`` auto-sizes
    (used for metadata columns, which we always show in full).
    """

    __slots__ = ("key", "label", "sort", "render", "meta_key", "width")

    def __init__(self, key: str, label: str, sort: Optional[Callable],
                 render: Callable[[Any, ProcInfo], Any],
                 meta_key: Optional[str] = None, width: Optional[int] = None):
        self.key = key
        self.label = label
        self.sort = sort
        self.render = render
        self.meta_key = meta_key
        self.width = width


def _user_cell(card: Any, p: ProcInfo) -> Text:
    return Text(f"● {p.user}", style=card.monitor.color_for(p.user))


def _w(key: str) -> Optional[int]:
    return config.COLUMN_WIDTHS.get(key)


def is_visible(key: str) -> bool:
    """Whether a built-in column is shown (config.COLUMN_VISIBLE, default True)."""
    return config.COLUMN_VISIBLE.get(key, True)


# MEM/GB is GPU memory; RAM/GB is host (CPU) memory. Widths come from config.
ALL_COLUMNS: List[Column] = [
    Column("PID", "PID", lambda p: p.pid, lambda c, p: str(p.pid), width=_w("PID")),
    Column("USER", "USER", lambda p: p.user.lower(), _user_cell, width=_w("USER")),
    Column("NO.", "NO.", lambda p: (p.user.lower(), p.number), lambda c, p: p.number, width=_w("NO.")),
    Column("MEM/GB", "MEM/GB", lambda p: p.mem, lambda c, p: fmt_gb(p.mem), width=_w("MEM/GB")),
    Column("RAM/GB", "RAM/GB", lambda p: p.rss, lambda c, p: fmt_gb(p.rss), width=_w("RAM/GB")),
    Column("RUNTIME", "RUNTIME", lambda p: p.runtime_sec, lambda c, p: p.runtime, width=_w("RUNTIME")),
    Column("SESSION", "SESSION", lambda p: p.sname.lower(), lambda c, p: p.sname, width=_w("SESSION")),
    Column("S.START", "S.START", lambda p: p.s_start_ts, lambda c, p: p.s_start, width=_w("S.START")),
    Column("COMMAND", "COMMAND", lambda p: p.cmd.lower(), lambda c, p: p.cmd, width=_w("COMMAND")),
]
COLS: Dict[str, Column] = {c.key: c for c in ALL_COLUMNS}

# The classic, show-everything layout (minus any columns hidden in config).
NORMAL_COLUMNS: List[Column] = [
    COLS[k] for k in ("PID", "USER", "NO.", "MEM/GB", "RAM/GB", "RUNTIME", "SESSION", "S.START", "COMMAND")
    if is_visible(k)
]
# Zen mode drops USER and S.START; metadata columns go in before COMMAND.
ZEN_FIXED_KEYS = ("PID", "NO.", "MEM/GB", "RAM/GB", "RUNTIME", "SESSION")
# The CPU card / tmux page are like zen but have no GPU memory column.
CPU_FIXED_KEYS = ("PID", "NO.", "RAM/GB", "RUNTIME", "SESSION")

# Columns that read most naturally largest-first on the initial click.
DESC_FIRST_KEYS = {"PID", "MEM/GB", "RAM/GB", "RUNTIME", "S.START"}


def meta_column(key: str) -> Column:
    """A column for a user-reported metadata field (progress bars animate)."""
    def render(card: Any, proc: ProcInfo, _key: str = key) -> Any:
        value = proc.meta.get(_key)
        if value is None:
            return Text("")
        if is_progress(value):
            return render_progress(value, getattr(card, "_frame", 0))
        return Text(str(value))

    return Column(key=f"meta:{key}", label=key.upper(),
                  sort=lambda p, _key=key: _meta_sort_value(p.meta.get(_key)),
                  render=render, meta_key=key)


def columns_with_meta(fixed_keys: Tuple[str, ...], procs: List[ProcInfo]) -> List[Column]:
    """Fixed columns + one column per reported field (first-seen order) + COMMAND."""
    fixed = [COLS[k] for k in fixed_keys if is_visible(k)]
    meta_keys: List[str] = []
    for proc in procs:
        for key in proc.meta:
            if key not in meta_keys:
                meta_keys.append(key)
    tail = [COLS["COMMAND"]] if is_visible("COMMAND") else []
    return (fixed + [meta_column(k) for k in meta_keys] + tail) or [COLS["PID"]]
