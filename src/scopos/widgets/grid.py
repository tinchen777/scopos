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
        # ETA estimated by the Monitor from how fast the bar has been moving.
        eta = data.get("eta")
        if eta is not None:
            tail = "done" if eta <= 0 else f"~{fmt_duration(eta)}"
            bar.append(f" · {tail}", style="green" if eta <= 0 else "cyan")
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


def fmt_gb(num_bytes: float) -> str:
    return "%.2f" % (num_bytes / (1024 ** 3))


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


def _progress_text(data: Dict[str, Any]) -> str:
    """A plain-text summary of a progress field, for tooltips / clipboard."""
    value = data.get("value")
    label = data.get("label")
    if value is None:
        return label or "…"
    shown = label if label is not None else f"{value * 100:.0f}%"
    eta = data.get("eta")
    if eta is not None:
        shown += " (done)" if eta <= 0 else f" (~{fmt_duration(eta)})"
    return shown


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

    def __init__(self, monitor: Monitor, zen: bool = False, pending: bool = False, danger: bool = False):
        super().__init__()
        self.monitor = monitor
        self.zen = zen
        # ``pending`` cards list a user's reported-but-not-yet-on-GPU processes.
        self.is_pending = pending
        # ``danger`` only changes what the right-click menu offers (adds Kill).
        self.danger = danger
        self.stats = Static(classes="stats")
        self.bar = MemoryBar()
        self.legend = Static(classes="legend")
        self.table = DataTable(zebra_stripes=True, cursor_type="row")
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

    def compose(self):
        yield self.stats
        if not self.is_pending:
            yield self.bar
            yield self.legend
        yield self.table

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

    # -- sorting -----------------------------------------------------------
    def on_data_table_header_selected(
        self,
        event: DataTable.HeaderSelected
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
            self._deferred = gpu
            return
        self._apply(gpu)

    def _apply(self, gpu: GPUInfo):
        self._deferred = None
        self._gpu = gpu
        if self.is_pending:
            self.border_title = " ⏳ PENDING  ·  not yet on GPU "
            self._update_pending_stats(gpu)
        else:
            self.border_title = f" # {gpu.index}  {gpu.name} "
            self._update_stats(gpu)
            self._update_bar(gpu)
            self._update_legend(gpu)
        self._update_table(gpu)

    def _update_pending_stats(self, gpu: GPUInfo):
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append("[PENDING] ", style="bold yellow")
        line.append(f"{len(gpu.procs)} proc(s)", style="bold")
        line.append("  ·  reported to scopos, waiting to allocate GPU memory", style="dim")
        self.stats.update(line)

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
            legend.append("🏆 " if user == mvp else "")
            if self.zen and bool(watch) and user == watch:
                # zen mode
                legend.append("★ ", style=color)
                legend.append(user, style=f"bold underline {color}")
            else:
                # normal mode
                legend.append(f"● {user}", style=color)
            legend.append(f" {fmt_gb(mem)} GB ({pct:.0f}%)   ")
        self.legend.update(legend)

    # -- table helpers -----------------------------------------------------
    def _visible_procs(self, gpu: GPUInfo) -> List[ProcInfo]:
        """Processes to list in the table: filtered to the watched user in zen."""
        if not self.is_pending and self.zen and self.monitor.watch_user:
            return [p for p in gpu.procs if p.user == self.monitor.watch_user]
        return list(gpu.procs)

    def _columns_for(self, procs: List[ProcInfo]) -> List[_Column]:
        # Pending cards are metadata-centric, so they always use the zen columns.
        if not self.zen and not self.is_pending:
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

        if procs:
            columns = self._columns_for(procs)
            self._columns = columns
            # column labels
            labels = []
            sort_func = None
            for col in columns:
                label = col.label
                if col.key == self._sort_key:
                    label = f"{label} {'▼' if self._sort_reverse else '▲'}"
                    if col.sort is not None:
                        sort_func = col.sort
                labels.append(label)
            self.table.add_columns(*labels)
            # sort process
            if sort_func is not None:
                procs = sorted(procs, key=sort_func, reverse=self._sort_reverse)
            self._row_procs = procs
            # rows
            for row_idx, proc in enumerate(procs):
                row = []
                for col_idx, col in enumerate(columns):
                    row.append(col.render(self, proc))
                    if col.meta_key is not None:
                        value = proc.meta.get(col.meta_key)
                        if value is not None and is_progress(value) and value.get("value") is None:
                            self._anim_cells.append((row_idx, col_idx, value))
                self.table.add_row(*row)
        else:
            self._row_procs = []
            self.table.add_columns("")  # at least one column is needed to show the "no processes" message
            msg = "— no compute processes —"
            if self.zen and self.monitor.watch_user:
                msg = f"— no processes for [{self.monitor.watch_user}] —"
            self.table.add_row(Text(msg, style="dim"))

    # -- interaction: hover, right-click menu, kill ------------------------
    def set_danger(self, danger: bool):
        """Danger mode only affects whether the menu offers Kill (no re-render)."""
        self.danger = danger

    def _hover_changed(self, coord: Optional[Coordinate]):
        """Show the full text of the hovered cell as the table's tooltip."""
        table = self.table
        if coord is None or not self._row_procs:
            table.tooltip = None
            return
        try:
            value = table.get_cell_at(coord)
        except Exception:
            table.tooltip = None
            return
        text = value.plain if isinstance(value, Text) else str(value)
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
        options: List[Tuple[str, str]] = [("copy", "📋  Copy row info")]
        if self.danger:
            options.append(("kill", f"💀  Kill process (PID {proc.pid})"))
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
            if is_progress(raw):
                return _progress_text(raw)
            return "" if raw is None else str(raw)
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
        msg = (
            f"⚠  Kill process PID {proc.pid} ({proc.user})?\n\n"
            f"{proc.cmd}\n\n"
            "This sends a terminate signal to the process and cannot be undone."
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
