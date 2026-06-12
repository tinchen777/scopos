# -*- coding: utf-8 -*-
"""Column definitions and cell rendering shared by every process table.

This is the low-level "what a row looks like" layer: the column registry, the
progress-bar renderer and small text helpers. It has no widgets of its own
(beyond producing Rich ``Text``), so both :mod:`scopos.widgets.proc_table` and
the cards build on it without any circular dependency.
"""

from __future__ import annotations
from rich.text import Text
from dataclasses import dataclass
from typing import (Any, Callable, Dict, List, Optional)

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
            undo_char = [">"] * (width - filled)
            idx = frame % width - filled
            if idx >= 0:
                undo_char[idx] = " "
            bar.append("".join(undo_char), style=track)
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
@dataclass
class Column:
    """One table column: how to label, sort and render it.

    ``render(card, proc)`` returns a cell value (``str`` or Rich ``Text``).
    ``meta_key`` marks columns sourced from a process's reported metadata, so the
    table can find the animated progress cells. ``width`` caps the cell width
    (the full value is still stored, so hovering shows it); ``None`` auto-sizes
    (used for metadata columns, which we always show in full).
    """
    key: str
    label: str
    sort: Optional[Callable[[ProcInfo], Any]]
    render: Callable[[Any, ProcInfo], Any]
    meta_key: Optional[str] = None
    width: Optional[int] = None


def _w(key: str) -> Optional[int]:
    return config.COLUMN_WIDTHS.get(key)


def _is_visible(key: str) -> bool:
    """Whether a built-in column is shown (config.COLUMN_VISIBLE, default True)."""
    return config.COLUMN_VISIBLE.get(key, True)

COLS: Dict[str, Column] = {
    "PID": Column(key="PID", label="PID", sort=lambda p: p.pid,
                  render=lambda _, p: str(p.pid), width=_w("PID")),
    "USER": Column(key="USER", label="USER", sort=lambda p: p.user.lower(),
                   render=lambda card, p: Text(f"● {p.user}", style=card.monitor.color_for(p.user)), width=_w("USER")),
    "NO.": Column(key="NO.", label="NO.", sort=lambda p: (p.user.lower(), p.number),
                  render=lambda card, p: Text(p.number, style=card.monitor.color_for(p.user)), width=_w("NO.")),
    "MEM/GB": Column(key="MEM/GB", label="MEM/GB", sort=lambda p: p.mem,
                     render=lambda _, p: fmt_gb(p.mem), width=_w("MEM/GB")),
    "RAM/GB": Column(key="RAM/GB", label="RAM/GB", sort=lambda p: p.rss,
                     render=lambda _, p: fmt_gb(p.rss), width=_w("RAM/GB")),
    "RUNTIME": Column(key="RUNTIME", label="RUNTIME", sort=lambda p: p.runtime_sec,
                      render=lambda _, p: p.runtime, width=_w("RUNTIME")),
    "SESSION": Column(key="SESSION", label="SESSION", sort=lambda p: p.s_alias,
                      render=lambda _, p: p.s_alias, width=_w("SESSION")),
    "S.START": Column(key="S.START", label="S.START", sort=lambda p: p.s_start_ts,
                      render=lambda _, p: p.s_start, width=_w("S.START")),
    "COMMAND": Column(key="COMMAND", label="COMMAND", sort=lambda p: p.cmd.lower(),
                      render=lambda _, p: p.cmd, width=_w("COMMAND"))
}
# global mode
GLOBAL_COLUMNS: List[Column] = list(COLS.values())
# zen mode
ZEN_KEYS = ("PID", "NO.", "MEM/GB", "RAM/GB", "RUNTIME", "SESSION")
ZEN_COLUMNS: List[Column] = [COLS[k] for k in ZEN_KEYS if _is_visible(k)]
# The CPU card / tmux page are like zen but have no GPU memory column.
CPU_KEYS = ("PID", "NO.", "RAM/GB", "RUNTIME", "SESSION")
CPU_COLUMNS: List[Column] = [COLS[k] for k in CPU_KEYS if _is_visible(k)]
# tmux mode
TMUX_KEYS = ("SESSION", "PID", "RAM/GB", "RUNTIME")
TMUX_COLUMNS: List[Column] = [COLS[k] for k in TMUX_KEYS if _is_visible(k)]

# Columns that read most naturally largest-first on the initial click.
REVERSE_KEYS = {"PID", "MEM/GB", "RAM/GB", "RUNTIME", "S.START"}


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


def columns_with_meta(fixed_cols: List[Column], procs: List[ProcInfo]) -> List[Column]:
    """Fixed columns + one column per reported field (first-seen order) + COMMAND."""
    meta_keys = list(dict.fromkeys(key for proc in procs for key in proc.meta))
    tail = [COLS["COMMAND"]] if _is_visible("COMMAND") else []
    return (fixed_cols + [meta_column(k) for k in meta_keys] + tail) or [COLS["PID"]]
