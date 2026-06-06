# -*- coding: utf-8 -*-
"""Reusable Textual widgets for Scopos."""

from __future__ import annotations
from rich.text import Text
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.widget import Widget
from textual.widgets import (DataTable, Static)
from typing import (Any, Callable, Dict, List, Optional, Tuple)

from .. import __version__
from ..metadata.utils import is_progress
from ..monitor import (GPUInfo, Monitor, ProcInfo, fmt_gb)


class MemoryBar(Widget):
    """A single-line bar whose coloured segments show each user's share.

    The bar always fills its own width, so it grows and shrinks with the
    terminal - that is what gives the "直观占比" (intuitive proportion) view.
    """

    DEFAULT_CSS = """
    MemoryBar {
        height: 1;
        width: 1fr;
    }
    """

    def __init__(self):
        super().__init__()
        self._segments: List[Tuple[str, float]] = []
        self._total: float = 1.0

    def set_data(self, segments: List[Tuple[str, float]], total: float):
        """segments: list of (colour, weight); total: the bar's full weight."""
        self._segments = segments
        self._total = total or 1.0
        self.refresh()

    def render(self) -> Text:
        width = self.size.width or 1
        text = Text(no_wrap=True, overflow="crop")
        used = 0
        for color, weight in self._segments:
            if weight <= 0:
                continue
            cells = round(weight / self._total * width)
            if cells == 0:
                cells = 1  # keep tiny-but-present users visible
            cells = min(cells, width - used)
            if cells <= 0:
                break
            text.append("█" * cells, style=color)
            used += cells
        if used < width:
            text.append("░" * (width - used), style="grey35")
        return text


# Width, in cells, of a rendered progress bar inside a table cell.
PROGRESS_WIDTH = 14


def _clamp01(value: Any) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, x))


def render_progress(data: Dict[str, Any], frame: int = 0, width: int = PROGRESS_WIDTH) -> Text:
    """Render a progress field (see :func:`scopos.metadata.make_progress`).

    A determinate bar fills proportionally to its value; an indeterminate bar
    (``value is None``) shows a block that bounces back and forth, advancing by
    ``frame`` so the TUI can animate it between refreshes.
    """
    value = data.get("value")
    label = data.get("label")
    color = data.get("color") or "cyan"
    bar = Text(no_wrap=True, overflow="crop")
    bar.append("▕", style="grey50")
    if value is None:
        block = max(2, width // 4)
        span = width - block
        if span <= 0:
            bar.append("█" * width, style=color)
        else:
            cycle = span * 2
            p = frame % cycle
            pos = p if p <= span else cycle - p
            bar.append("░" * pos, style="grey35")
            bar.append("█" * block, style=color)
            bar.append("░" * (span - pos), style="grey35")
        bar.append("▏", style="grey50")
        bar.append(f" {label}" if label else " …", style="dim")
    else:
        frac = _clamp01(value)
        filled = max(0, min(width, int(round(frac * width))))
        bar.append("█" * filled, style=color)
        bar.append("░" * (width - filled), style="grey35")
        bar.append("▏", style="grey50")
        shown = label if label is not None else f"{frac * 100:.0f}%"
        bar.append(f" {shown}", style="dim")
    return bar


class _Column:
    """One table column: how to label, sort and render it.

    ``render`` is called as ``render(card, proc)`` and returns a cell value
    (a ``str`` or Rich ``Text``).  ``meta_key`` is set for columns that come
    from a process's reported metadata, so the card can find animated
    progress cells.
    """

    __slots__ = ("key", "label", "sort", "render", "meta_key")

    def __init__(
        self,
        key: str,
        label: str,
        sort: Optional[Callable],
        render: Callable[["GpuCard", ProcInfo], Any],
        meta_key: Optional[str] = None,
    ):
        self.key = key
        self.label = label
        self.sort = sort
        self.render = render
        self.meta_key = meta_key


def _user_cell(card: "GpuCard", p: ProcInfo) -> Text:
    return Text(f"● {p.user}", style=card.monitor.color_for(p.user))


# The classic, show-everything layout.
NORMAL_COLUMNS: List[_Column] = [
    _Column("PID", "PID", lambda p: p.pid, lambda c, p: str(p.pid)),
    _Column("USER", "USER", lambda p: p.user.lower(), _user_cell),
    _Column("NO.", "NO.", lambda p: (p.user.lower(), p.number), lambda c, p: p.number),
    _Column("MEM/GB", "MEM/GB", lambda p: p.mem, lambda c, p: fmt_gb(p.mem)),
    _Column("RUNTIME", "RUNTIME", lambda p: p.runtime_sec, lambda c, p: p.runtime),
    _Column("SESSION", "SESSION", lambda p: p.sname.lower(), lambda c, p: p.sname),
    _Column("S.START", "S.START", lambda p: p.s_start_ts, lambda c, p: p.s_start),
    _Column("COMMAND", "COMMAND", lambda p: p.cmd.lower(), lambda c, p: p.cmd),
]

# Zen mode drops USER and S.START; metadata columns are inserted before COMMAND.
ZEN_FIXED: List[_Column] = [
    NORMAL_COLUMNS[0],  # PID
    NORMAL_COLUMNS[2],  # NO.
    NORMAL_COLUMNS[3],  # MEM/GB
    NORMAL_COLUMNS[4],  # RUNTIME
    NORMAL_COLUMNS[5],  # SESSION
]
ZEN_COMMAND = NORMAL_COLUMNS[7]

# Columns that read most naturally largest-first on the initial click.
DESC_FIRST_KEYS = {"PID", "MEM/GB", "RUNTIME", "S.START"}


def _meta_sort_value(value: Any):
    """A sort key for a metadata cell that never compares across types."""
    if is_progress(value):
        v = value.get("value")
        return (0, -1.0 if v is None else float(v))
    if value is None:
        return (2, "")
    try:
        return (0, float(value))
    except (TypeError, ValueError):
        return (1, str(value).lower())


class GpuCard(Vertical):
    """One GPU: header, stats line, proportion bar, legend and process table."""

    DEFAULT_CSS = """
    GpuCard {
        height: auto;
        border: round $primary;
        border-title-color: $text;
        border-title-style: bold;
        padding: 0 1;
        margin: 0;
    }
    GpuCard .stats { height: 1; }
    GpuCard .legend { height: auto; color: $text-muted; }
    GpuCard DataTable {
        height: auto;
        max-height: 20;
        margin-top: 1;
    }
    """

    def __init__(self, monitor: Monitor, zen: bool = False):
        super().__init__()
        self.monitor = monitor
        self.zen = zen
        self.stats = Static(classes="stats")
        self.bar = MemoryBar()
        self.legend = Static(classes="legend")
        self.table = DataTable(zebra_stripes=True, cursor_type="row")
        self._pending: Optional[GPUInfo] = None
        self._gpu: Optional[GPUInfo] = None
        self._sort_key: Optional[str] = None
        self._sort_reverse: bool = False
        self._columns: List[_Column] = NORMAL_COLUMNS
        # (row, column, progress-data) for indeterminate bars we animate.
        self._anim_cells: List[Tuple[int, int, Dict[str, Any]]] = []
        self._frame: int = 0

    def compose(self):
        yield self.stats
        yield self.bar
        yield self.legend
        yield self.table

    def on_mount(self):
        if self._pending is not None:
            self._apply(self._pending)

    # -- mode --------------------------------------------------------------
    def set_zen(self, zen: bool):
        if zen == self.zen:
            return
        self.zen = zen
        if self._gpu is not None and self.is_mounted:
            self._apply(self._gpu)

    # -- sorting -----------------------------------------------------------
    def on_data_table_header_selected(
        self, event: DataTable.HeaderSelected
    ):
        event.stop()
        idx = event.column_index
        if idx >= len(self._columns):
            return
        col = self._columns[idx]
        if col.sort is None:
            return
        if self._sort_key == col.key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key = col.key
            self._sort_reverse = col.key in DESC_FIRST_KEYS
        if self._gpu is not None:
            self._update_table(self._gpu)

    # -- animation ---------------------------------------------------------
    def animate_progress(self, frame: int):
        """Re-render indeterminate progress cells so their blocks move."""
        self._frame = frame
        if not self._anim_cells:
            return
        for row, col, data in self._anim_cells:
            try:
                self.table.update_cell_at(
                    Coordinate(row, col),
                    render_progress(data, frame),
                    update_width=False,
                )
            except Exception:
                # Table was rebuilt out from under us; the next refresh fixes it.
                pass

    # -- updating ----------------------------------------------------------
    def update(self, gpu: GPUInfo):
        # A card may be updated in the same frame it is mounted, before its
        # columns exist; defer until on_mount in that case.
        if not self.is_mounted:
            self._pending = gpu
            return
        self._apply(gpu)

    def _apply(self, gpu: GPUInfo):
        self._pending = None
        self._gpu = gpu
        self.border_title = f" # {gpu.index}  {gpu.name} "
        self._update_stats(gpu)
        self._update_bar(gpu)
        self._update_legend(gpu)
        self._update_table(gpu)

    def _update_stats(self, gpu: GPUInfo):
        rate = gpu.idle_rate
        if rate <= 0.15:
            free_style = "bold red"
        elif rate <= 0.5:
            free_style = "bold yellow"
        else:
            free_style = "bold green"

        line = Text(no_wrap=True, overflow="ellipsis")
        # PROC
        line.append("[PROC] ", style="bold")
        line.append(f"{len(gpu.procs)}    ", style="bold")
        # USED
        line.append("[USED] ", style="bold")
        line.append(f"{fmt_gb(gpu.mem_used)}", style="bold")
        line.append(f" / {fmt_gb(gpu.mem_total)} GB", style="dim")
        line.append(f" ({gpu.used_rate * 100:.0f}%)    ")
        # FREE
        line.append("[FREE] ", style="bold")
        line.append(f"{fmt_gb(gpu.mem_free)} GB ", style=free_style)
        # ⚡
        if gpu.util >= 0:
            line.append("   [⚡] ", style="bold")
            line.append(f"{gpu.util}%", style="cyan")
        # 🌡
        if gpu.temperature >= 0:
            line.append("   [🌡] ", style="bold")
            temp_style = "red" if gpu.temperature >= 80 else "cyan"
            line.append(f"{gpu.temperature}°C", style=temp_style)
        self.stats.update(line)

    def _update_bar(self, gpu: GPUInfo):
        ordered = sorted(gpu.user_mems.items(), key=lambda kv: kv[1], reverse=True)
        segments = [(self.monitor.color_for(u), float(m)) for u, m in ordered]
        self.bar.set_data(segments, float(gpu.mem_total))

    def _update_legend(self, gpu: GPUInfo):
        ordered = sorted(gpu.user_mems.items(), key=lambda kv: kv[1], reverse=True)
        legend = Text(no_wrap=True, overflow="ellipsis")
        if not ordered:
            legend.append("idle", style="dim")
            self.legend.update(legend)
            return
        mvp = ordered[0][0]
        watch = self.monitor.watch_user
        for user, mem in ordered:
            color = self.monitor.color_for(user)
            pct = mem / gpu.mem_total * 100 if gpu.mem_total else 0
            # In zen mode the watched user is highlighted in the legend even
            # though the table is filtered down to just their processes.
            highlighted = self.zen and bool(watch) and user == watch
            name_style = f"bold underline {color}" if highlighted else color
            legend.append("🏆 " if user == mvp else "")
            legend.append("★ " if highlighted else "")
            legend.append("● ", style=color)
            legend.append(user, style=name_style)
            legend.append(f" {fmt_gb(mem)} GB ({pct:.0f}%)   ")
        self.legend.update(legend)

    # -- table helpers -----------------------------------------------------
    def _visible_procs(self, gpu: GPUInfo) -> List[ProcInfo]:
        """Processes to list in the table: filtered to the watched user in zen."""
        if self.zen and self.monitor.watch_user:
            return [p for p in gpu.procs if p.user == self.monitor.watch_user]
        return list(gpu.procs)

    def _columns_for(self, procs: List[ProcInfo]) -> List[_Column]:
        if not self.zen:
            return NORMAL_COLUMNS
        # Build one column per reported field, in first-seen order across the
        # visible processes, then keep COMMAND last.
        meta_keys: List[str] = []
        for proc in procs:
            for key in proc.meta:
                if key not in meta_keys:
                    meta_keys.append(key)
        return ZEN_FIXED + [self._meta_column(key) for key in meta_keys] + [ZEN_COMMAND]

    def _meta_column(self, key: str) -> _Column:
        def render(card: "GpuCard", proc: ProcInfo, _key: str = key) -> Any:
            value = proc.meta.get(_key)
            if value is None:
                return Text("")
            if is_progress(value):
                return render_progress(value, card._frame)
            return Text(str(value))

        return _Column(
            key=f"meta:{key}",
            label=key.upper(),
            sort=lambda p, _key=key: _meta_sort_value(p.meta.get(_key)),
            render=render,
            meta_key=key,
        )

    def _update_table(self, gpu: GPUInfo):
        # Rebuild columns each time so the sort arrow can move between headers
        # and zen-mode metadata columns can appear/disappear with the data.
        self.table.clear(columns=True)
        self._anim_cells = []

        procs = self._visible_procs(gpu)
        columns = self._columns_for(procs)
        self._columns = columns

        labels = []
        for col in columns:
            label = col.label
            if col.key == self._sort_key:
                label = f"{label} {'▼' if self._sort_reverse else '▲'}"
            labels.append(label)
        self.table.add_columns(*labels)

        if self._sort_key is not None:
            col = next(
                (c for c in columns if c.key == self._sort_key and c.sort), None
            )
            if col is not None:
                procs = sorted(procs, key=col.sort, reverse=self._sort_reverse)

        if not procs:
            empty = [Text("", style="dim") for _ in columns]
            msg = "— no compute processes —"
            if self.zen and self.monitor.watch_user:
                msg = f"— no processes for [{self.monitor.watch_user}] —"
            empty[1 if len(columns) > 1 else 0] = Text(msg, style="dim")
            self.table.add_row(*empty)
            return

        for row_idx, proc in enumerate(procs):
            row = []
            for col_idx, col in enumerate(columns):
                row.append(col.render(self, proc))
                if col.meta_key is not None:
                    value = proc.meta.get(col.meta_key)
                    if is_progress(value) and value.get("value") is None:
                        self._anim_cells.append((row_idx, col_idx, value))
            self.table.add_row(*row)
