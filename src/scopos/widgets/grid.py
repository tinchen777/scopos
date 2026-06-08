# -*- coding: utf-8 -*-
"""Reusable Textual widgets for Scopos."""

from __future__ import annotations
import psutil
from rich.text import Text
from textual import events
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.widget import Widget
from textual.widgets import (DataTable, Static)
from typing import (Any, Callable, Dict, List, Optional, Tuple)

from ..metadata.utils import is_progress
from ..monitor import (GPUInfo, Monitor, ProcInfo, fmt_duration)
from .dialogs import (ConfirmScreen, ContextMenu)
from .. import config


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
            text.append("░" * (width - used), style=config.BAR_TRACK_COLOR)
        return text


Spinner_1 = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]
Spinner_2 = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
Waiting = " waiting..."


def _progress_text(data: Dict[str, Any]) -> str:
    """A plain-text summary of a progress field, for tooltips / clipboard."""
    shown = str(data.get("label"))
    eta = data.get("eta")
    if eta is not None:
        shown += " [DONE]" if eta <= 0 else f" [~{fmt_duration(eta)}]"
    return shown


def render_progress(data: Dict[str, Any], frame: int = 0, width: int = config.PROGRESS_WIDTH) -> Text:
    """Render a progress field (see :func:`scopos.metadata.make_progress`).

    A determinate bar fills proportionally to its value; an indeterminate bar
    (``value is None``) shows a block that bounces back and forth, advancing by
    ``frame`` so the TUI can animate it between refreshes.
    """
    frac = data.get("frac")
    label = data.get("label")
    color = data.get("color") or config.PROGRESS_COLOR
    label_color = config.PROGRESS_LABEL_COLOR
    track = config.BAR_TRACK_COLOR
    width = int(max(1, width))
    bar = Text(no_wrap=True, overflow="crop")
    if frac is None:
        # block = 1
        # span = width - block
        # if span <= 0:
        #     bar.append("█" * width, style=color)
        # else:
        #     cycle = span * 2
        #     p = frame % cycle
        #     pos = p if p <= span else cycle - p
        #     bar.append("<" * pos, style=track)
        #     bar.append("■" * block, style=color)
        #     bar.append(">" * (span - pos), style=track)
        # bar.append("▏", style="grey50")

        pos = frame % len(Spinner_1)
        animate_str = Text(Spinner_1[pos], style=color)
        span = width - len(Waiting) - 1
        if span >= 0:
            animate_str.append(Waiting, style=color)
        animate_str.align("center", width + 3)
        bar.append(animate_str)
    else:
        bar.append("▕", style="grey50")
        if 0.0 <= frac <= 1.0:
            filled = int(round(frac * width))
            bar.append("■" * filled, style=color)
            bar.append(">" * (width - filled), style=track)
        else:
            bar.append("-" * width, style=track)
        bar.append("▏", style="grey50")
    # label
    bar.append(str(label), style=label_color)
    # ETA estimated by the Monitor from how fast the bar has been moving.
    eta = data.get("eta")
    if eta is not None:
        bar.append(" [")
        if eta <= 0:
            bar.append("DONE", style=config.COLOR_OK)
        else:
            bar.append(f"~{fmt_duration(eta)}", style="yellow")
        bar.append("]")

    return bar


class _Column:
    """One table column: how to label, sort and render it.

    ``render`` is called as ``render(card, proc)`` and returns a cell value
    (a ``str`` or Rich ``Text``).  ``meta_key`` is set for columns that come
    from a process's reported metadata, so the card can find animated
    progress cells.  ``width`` caps how many cells the column takes; the full
    value is still stored, so hovering a clipped cell shows it in a tooltip.
    A ``width`` of ``None`` lets the column auto-size to its content (used for
    user-reported metadata columns, which we always show in full).
    """

    __slots__ = ("key", "label", "sort", "render", "meta_key", "width")

    def __init__(
        self,
        key: str,
        label: str,
        sort: Optional[Callable],
        render: Callable[["Card", ProcInfo], Any],
        meta_key: Optional[str] = None,
        width: Optional[int] = None,
    ):
        self.key = key
        self.label = label
        self.sort = sort
        self.render = render
        self.meta_key = meta_key
        self.width = width


def _user_cell(card: "Card", p: ProcInfo) -> Text:
    return Text(f"● {p.user}", style=card.monitor.color_for(p.user))


def _fmt_gb(num_bytes: float) -> str:
    return "%.2f" % (num_bytes / (1024 ** 3))


# All known columns, keyed for reuse across the normal / zen / CPU layouts.
# MEM/GB is GPU memory; RAM/GB is host (CPU) memory, shown everywhere.
# Widths come from ``config.COLUMN_WIDTHS`` so the layout can be tuned in one place.
def _w(key: str) -> Optional[int]:
    return config.COLUMN_WIDTHS.get(key)


def _visible(key: str) -> bool:
    """Whether a built-in column is shown (config.COLUMN_VISIBLE, default True)."""
    return config.COLUMN_VISIBLE.get(key, True)


ALL_COLUMNS: List[_Column] = [
    _Column("PID", "PID", lambda p: p.pid, lambda c, p: str(p.pid), width=_w("PID")),
    _Column("USER", "USER", lambda p: p.user.lower(), _user_cell, width=_w("USER")),
    _Column("NO.", "NO.", lambda p: (p.user.lower(), p.number), lambda c, p: p.number, width=_w("NO.")),
    _Column("MEM/GB", "MEM/GB", lambda p: p.mem, lambda c, p: _fmt_gb(p.mem), width=_w("MEM/GB")),
    _Column("RAM/GB", "RAM/GB", lambda p: p.rss, lambda c, p: _fmt_gb(p.rss), width=_w("RAM/GB")),
    _Column("RUNTIME", "RUNTIME", lambda p: p.runtime_sec, lambda c, p: p.runtime, width=_w("RUNTIME")),
    _Column("SESSION", "SESSION", lambda p: p.sname.lower(), lambda c, p: p.sname, width=_w("SESSION")),
    _Column("S.START", "S.START", lambda p: p.s_start_ts, lambda c, p: p.s_start, width=_w("S.START")),
    _Column("COMMAND", "COMMAND", lambda p: p.cmd.lower(), lambda c, p: p.cmd, width=_w("COMMAND")),
]
COLS: Dict[str, _Column] = {c.key: c for c in ALL_COLUMNS}


# The classic, show-everything layout (minus any columns hidden in config).
NORMAL_COLUMNS: List[_Column] = [
    COLS[k] for k in ("PID", "USER", "NO.", "MEM/GB", "RAM/GB", "RUNTIME", "SESSION", "S.START", "COMMAND")
    if _visible(k)
]
# Zen mode drops USER and S.START; metadata columns go in before COMMAND.
ZEN_FIXED_KEYS = ("PID", "NO.", "MEM/GB", "RAM/GB", "RUNTIME", "SESSION")
# The CPU card is like zen but has no GPU memory column.
CPU_FIXED_KEYS = ("PID", "NO.", "RAM/GB", "RUNTIME", "SESSION")

# Columns that read most naturally largest-first on the initial click.
DESC_FIRST_KEYS = {"PID", "MEM/GB", "RAM/GB", "RUNTIME", "S.START"}


def _fit_cell(value: Any, width: Optional[int]) -> Any:
    """Clip a cell's text to ``width`` with an ellipsis (… ); ``None`` = no clip.

    Only what's drawn in the cell is shortened — the hover tooltip recomputes
    the full value from the process, so nothing is lost.
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


class Card(Vertical):
    """Base card: a header, a stats line and a process table.

    All of the shared behaviour lives here — sorting, progress animation, hover
    tooltips and the right-click copy/kill menu, plus the generic metadata-style
    table layout.  Subclasses fill in the small differences:

    * :class:`GpuCard` — one physical GPU; adds a memory bar + per-user legend,
      shows every column in normal mode and the metadata layout in zen mode.
    * :class:`CpuCard` — the watched user's non-GPU, scopos-reporting processes;
      a host-RAM view with no GPU memory column.

    The two are parallel siblings (much like ``Monitor`` / ``DemoMonitor``).
    """

    # Layout-driven bits (padding, max width/height) come from scopos.config.
    # A ``Card`` type selector also matches the GpuCard/CpuCard subclasses.
    DEFAULT_CSS = f"""
    Card {{
        height: auto;
        max-width: {config.CARD_MAX_WIDTH or '100%'};
        border: round $primary;
        border-title-color: $text;
        border-title-style: bold;
        padding: {config.CARD_PADDING[0]} {config.CARD_PADDING[1]};
        margin: 0;
    }}
    Card .stats {{ height: 1; }}
    Card .legend {{ height: auto; color: $text-muted; }}
    Card DataTable {{
        height: auto;
        max-height: {config.TABLE_MAX_HEIGHT};
        margin-top: 1;
    }}
    """

    def __init__(self, monitor: Monitor, zen: bool = False, danger: bool = False):
        super().__init__()
        self.monitor = monitor
        self.zen = zen
        # ``danger`` only changes what the right-click menu offers (adds Kill).
        self.danger = danger
        self.stats = Static(classes="stats")
        self.table = DataTable(
            zebra_stripes=True,
            cursor_type="row",
            cell_padding=config.TABLE_CELL_PADDING,
        )
        self._deferred: Optional[GPUInfo] = None  # update arriving before mount
        self._gpu: Optional[GPUInfo] = None
        self._sort_key: Optional[str] = None
        self._sort_reverse: bool = False
        self._columns: List[_Column] = NORMAL_COLUMNS
        # The processes currently shown, in row order, for hover/right-click.
        self._row_procs: List[ProcInfo] = []
        # (row, column, progress-data) for indeterminate bars we animate.
        self._anim_cells: List[Tuple[int, int, Dict[str, Any]]] = []
        self._frame: int = 0

    # -- composition -------------------------------------------------------
    def compose(self):
        yield self.stats
        yield from self._extra_widgets()
        yield self.table

    def _extra_widgets(self):
        """Header widgets between the stats line and the table (GPU adds a bar)."""
        return iter(())

    def on_mount(self):
        # Keep the table's tooltip in sync with the cell under the mouse so a
        # long, truncated column can be read in full just by hovering it.
        self.watch(self.table, "hover_coordinate", self._hover_changed)
        if self._deferred is not None:
            self._apply(self._deferred)

    # -- mode --------------------------------------------------------------
    def set_zen(self, zen: bool):
        if zen == self.zen:
            return
        self.zen = zen
        if self._gpu is not None and self.is_mounted:
            self._apply(self._gpu)

    def set_danger(self, danger: bool):
        """Danger mode only affects whether the menu offers Kill (no re-render)."""
        self.danger = danger

    # -- sorting -----------------------------------------------------------
    def on_data_table_header_selected(self, event: DataTable.HeaderSelected):
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
            self._deferred = gpu
            return
        self._apply(gpu)

    def _apply(self, gpu: GPUInfo):
        self._deferred = None
        self._gpu = gpu
        self._render_header(gpu)
        self._update_table(gpu)

    # -- header / columns / procs (subclass-specific) ----------------------
    def _render_header(self, gpu: GPUInfo):
        """Set the border title and stats line (and any header widgets)."""
        raise NotImplementedError

    def _fixed_keys(self) -> Tuple[str, ...]:
        """Built-in columns (besides metadata + COMMAND) for the zen layout."""
        return ZEN_FIXED_KEYS

    def _visible_procs(self, gpu: GPUInfo) -> List[ProcInfo]:
        return list(gpu.procs)

    def _empty_message(self) -> str:
        return "— no compute processes —"

    def _columns_for(self, procs: List[ProcInfo]) -> List[_Column]:
        # Metadata-centric layout: fixed columns + one column per reported field
        # (first-seen order across the visible processes) + COMMAND last.
        fixed = [COLS[k] for k in self._fixed_keys() if _visible(k)]
        meta_keys: List[str] = []
        for proc in procs:
            for key in proc.meta:
                if key not in meta_keys:
                    meta_keys.append(key)
        tail = [COLS["COMMAND"]] if _visible("COMMAND") else []
        cols = fixed + [self._meta_column(key) for key in meta_keys] + tail
        return cols or [COLS["PID"]]  # never leave the table column-less

    def _meta_column(self, key: str) -> _Column:
        def render(card: "Card", proc: ProcInfo, _key: str = key) -> Any:
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
        # and metadata columns can appear/disappear with the data.
        self.table.clear(columns=True)
        self._anim_cells = []

        procs = self._visible_procs(gpu)

        if procs:
            columns = self._columns_for(procs)
            self._columns = columns
            # column labels
            for col in columns:
                label = col.label
                if col.key == self._sort_key:
                    label = label if col.width is None else label.center(col.width)
                    label = Text(label, style="reverse" if self._sort_reverse else "reverse underline")
                    if col.sort is not None:
                        procs = sorted(procs, key=col.sort, reverse=self._sort_reverse)
                self.table.add_column(label, width=col.width)
            self._row_procs = procs
            # rows
            for row_idx, proc in enumerate(procs):
                row = []
                for col_idx, col in enumerate(columns):
                    row.append(_fit_cell(col.render(self, proc), col.width))
                    if col.meta_key is not None:
                        value = proc.meta.get(col.meta_key)
                        if value is not None and is_progress(value) and value.get("frac") is None:
                            self._anim_cells.append((row_idx, col_idx, value))
                self.table.add_row(*row)
        else:
            self._row_procs = []
            self.table.add_columns("")  # at least one column is needed to show the message
            self.table.add_row(Text(self._empty_message(), style="dim"))

    # -- interaction: hover, right-click menu, kill ------------------------
    def _hover_changed(self, coord: Optional[Coordinate]):
        """Show the full (untruncated) value of the hovered cell as a tooltip.

        Recomputed from the process + column rather than read from the table,
        so it stays complete even though the cell itself is clipped.
        """
        table = self.table
        if (
            coord is None
            or not (0 <= coord.row < len(self._row_procs))
            or not (0 <= coord.column < len(self._columns))
        ):
            table.tooltip = None
            return
        proc = self._row_procs[coord.row]
        col = self._columns[coord.column]
        text = self._clean_cell(col, proc)
        table.tooltip = text or None

    def on_mouse_down(self, event: events.MouseDown):
        # Right-click (button 3) on a process row opens the context menu.
        if event.button != 3:
            return
        coord = self.table.hover_coordinate
        if coord is None or not (0 <= coord.row < len(self._row_procs)):
            return
        event.stop()
        event.prevent_default()
        proc = self._row_procs[coord.row]
        options: List[Tuple[str, str]] = [("copy", "📋 Copy info")]
        if self.danger:
            options.append(("kill", f"💀 Kill process (PID {proc.pid})"))
        x = getattr(event, "screen_x", event.x)
        y = getattr(event, "screen_y", event.y)
        self.app.push_screen(
            ContextMenu(options, x, y),
            lambda choice, p=proc: self._on_menu(choice, p),
        )

    def _on_menu(self, choice: Optional[str], proc: ProcInfo):
        if choice == "copy":
            self._copy_proc(proc)
        elif choice == "kill":
            self._confirm_kill(proc)

    def _row_info(self, proc: ProcInfo) -> str:
        lines = []
        for col in self._columns:
            lines.append(f"{col.label}: {self._clean_cell(col, proc)}".rstrip())
        return "\n".join(lines)

    def _clean_cell(self, col: _Column, proc: ProcInfo) -> str:
        """A clean, plain-text value for a cell (progress bars become text)."""
        if col.meta_key is not None:
            raw = proc.meta.get(col.meta_key)
            if raw is None:
                return ""
            return _progress_text(raw) if is_progress(raw) else str(raw)
        value = col.render(self, proc)
        return value.plain if isinstance(value, Text) else str(value)

    def _copy_proc(self, proc: ProcInfo):
        info = self._row_info(proc)
        try:
            self.app.copy_to_clipboard(info)
            self._notify(f"Copied info for PID {proc.pid}")
        except Exception as exc:
            self._notify(f"Copy failed: {exc}", error=True)

    def _confirm_kill(self, proc: ProcInfo):
        # Show the full row (same fields as "copy info") so it's easy to be sure
        # this is the right process before sending a signal.
        msg = (
            "⚠ Kill this process?\nThis sends a terminate signal and cannot be undone.\n\n"
            f"{self._row_info(proc)}"
        )
        self.app.push_screen(
            ConfirmScreen(msg),
            lambda ok, p=proc: self._do_kill(p) if ok else None,
        )

    def _do_kill(self, proc: ProcInfo):
        try:
            psutil.Process(proc.pid).terminate()
            self._notify(f"Sent terminate signal to PID {proc.pid}")
        except psutil.NoSuchProcess:
            self._notify(f"Process {proc.pid} no longer exists")
        except psutil.AccessDenied:
            self._notify(f"Permission denied: cannot kill PID {proc.pid}", error=True)
        except Exception as exc:
            self._notify(f"Kill failed: {exc}", error=True)

    def _notify(self, message: str, error: bool = False):
        try:
            self.app.notify(message, severity="error" if error else "information")
        except Exception:
            pass


class GpuCard(Card):
    """One GPU: header, stats line, proportion bar, legend and process table."""

    def __init__(self, monitor: Monitor, zen: bool = False, danger: bool = False):
        super().__init__(monitor, zen=zen, danger=danger)
        self.bar = MemoryBar()
        self.legend = Static(classes="legend")

    def _extra_widgets(self):
        yield self.bar
        yield self.legend

    def _render_header(self, gpu: GPUInfo):
        self.border_title = f" # {gpu.index}  <{gpu.name}> "
        self._update_stats(gpu)
        self._update_bar(gpu)
        self._update_legend(gpu)

    # Normal mode shows every column; zen mode uses the metadata layout (base).
    def _columns_for(self, procs: List[ProcInfo]) -> List[_Column]:
        if not self.zen:
            return NORMAL_COLUMNS or [COLS["PID"]]
        return super()._columns_for(procs)

    def _visible_procs(self, gpu: GPUInfo) -> List[ProcInfo]:
        """In zen mode the table is filtered down to the watched user."""
        if self.zen and self.monitor.watch_user:
            return [p for p in gpu.procs if p.user == self.monitor.watch_user]
        return list(gpu.procs)

    def _empty_message(self) -> str:
        if self.zen and self.monitor.watch_user:
            return f"— no processes for [{self.monitor.watch_user}] —"
        return "— no compute processes —"

    def _update_stats(self, gpu: GPUInfo):
        rate = gpu.idle_rate
        if rate <= config.MEM_FREE_CRIT:
            free_style = f"bold {config.COLOR_CRIT}"
        elif rate <= config.MEM_FREE_WARN:
            free_style = f"bold {config.COLOR_WARN}"
        else:
            free_style = f"bold {config.COLOR_OK}"

        line = Text(no_wrap=True, overflow="ellipsis")
        # PROC
        line.append("[PROC] ", style="bold")
        line.append(f"{len(gpu.procs)}    ", style="bold")
        # USED
        line.append("[USED] ", style="bold")
        line.append(f"{_fmt_gb(gpu.mem_used)}", style="bold")
        line.append(f" / {_fmt_gb(gpu.mem_total)} GB", style="dim")
        line.append(f" ({gpu.used_rate * 100:.0f}%)    ")
        # FREE
        line.append("[FREE] ", style="bold")
        line.append(f"{_fmt_gb(gpu.mem_free)} GB ", style=free_style)
        # ⚡
        if gpu.util >= 0:
            line.append("   [⚡] ", style="bold")
            line.append(f"{gpu.util}%", style=config.TEMP_COLOR)
        # 🌡
        if gpu.temperature >= 0:
            line.append("   [🌡] ", style="bold")
            temp_style = config.COLOR_CRIT if gpu.temperature >= config.TEMP_WARN_C else config.TEMP_COLOR
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
            legend.append("🏆 " if user == mvp else "")
            if self.zen and bool(watch) and user == watch:
                # zen mode
                legend.append("★ ", style=color)
                legend.append(user, style=f"bold underline {color}")
            else:
                # normal mode
                legend.append(f"● {user}", style=color)
            legend.append(f" {_fmt_gb(mem)} GB ({pct:.0f}%)   ")
        self.legend.update(legend)


class CpuCard(Card):
    """The watched user's non-GPU, scopos-reporting processes (host-RAM view).

    Parallel to :class:`GpuCard` but with no GPU memory column and no bar/legend;
    it extends scopos to plain CPU jobs and to jobs not yet on a GPU. A job that
    later allocates GPU memory shows up under its GPU card automatically.
    """

    def _render_header(self, gpu: GPUInfo):
        self.border_title = " 🧮  <CPU>  ·  tracked process(es) from scopos API "
        rss_total = sum(p.rss for p in gpu.procs)
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(f"[PROC] {len(gpu.procs)}", style="bold")
        line.append(f"    [RAM] {_fmt_gb(rss_total)} GB", style="bold")
        line.append(f"  ·  host-memory view of {self.monitor.watch_user}'s scopos-reporting jobs", style="dim")
        self.stats.update(line)

    def _fixed_keys(self) -> Tuple[str, ...]:
        return CPU_FIXED_KEYS

    def _empty_message(self) -> str:
        return "— no scopos-reporting processes —"
