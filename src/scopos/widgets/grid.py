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
from ..monitor import (DeviceInfo, GPUInfo, Monitor, ProcInfo, fmt_duration)
from .dialogs import (ConfirmScreen, ContextMenu)
from .. import config
from ._utils import fmt_gb


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
        render: Callable[[DeviceCard, ProcInfo], Any],
        meta_key: Optional[str] = None,
        width: Optional[int] = None,
    ):
        self.key = key
        self.label = label
        self.sort = sort
        self.render = render
        self.meta_key = meta_key
        self.width = width


def _user_cell(card: DeviceCard, p: ProcInfo) -> Text:
    return Text(f"● {p.user}", style=card.monitor.color_for(p.user))


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
    _Column("MEM/GB", "MEM/GB", lambda p: p.mem, lambda c, p: fmt_gb(p.mem), width=_w("MEM/GB")),
    _Column("RAM/GB", "RAM/GB", lambda p: p.rss, lambda c, p: fmt_gb(p.rss), width=_w("RAM/GB")),
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


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def meta_column(key: str) -> _Column:
    """A column for a user-reported metadata field (progress bars animate)."""
    def render(card: Any, proc: ProcInfo, _key: str = key) -> Any:
        value = proc.meta.get(_key)
        if value is None:
            return Text("")
        if is_progress(value):
            return render_progress(value, getattr(card, "_frame", 0))
        return Text(str(value))

    return _Column(
        key=f"meta:{key}",
        label=key.upper(),
        sort=lambda p, _key=key: _meta_sort_value(p.meta.get(_key)),
        render=render,
        meta_key=key,
    )


def columns_with_meta(fixed_keys: Tuple[str, ...], procs: List[ProcInfo]) -> List[_Column]:
    """Fixed columns + one column per reported field (first-seen order) + COMMAND."""
    fixed = [COLS[k] for k in fixed_keys if _visible(k)]
    meta_keys: List[str] = []
    for proc in procs:
        for key in proc.meta:
            if key not in meta_keys:
                meta_keys.append(key)
    tail = [COLS["COMMAND"]] if _visible("COMMAND") else []
    return (fixed + [meta_column(k) for k in meta_keys] + tail) or [COLS["PID"]]


# Width of the leading checkbox column shown while batch-selecting in kill mode.
CHECK_WIDTH = 3


class ProcTable(Vertical):
    """A reusable process table: sorting, hover tooltips, a right-click
    copy/kill menu, cursor persistence across refreshes, and — when kill mode is
    armed — batch selection via a leading checkbox column.

    The owner supplies a ``columns_for(procs) -> [_Column]`` callback and feeds
    rows with :meth:`update`. This is the single home for all the table
    interaction shared by the GPU/CPU cards and the tmux view.
    """

    DEFAULT_CSS = f"""
    ProcTable {{ height: auto; }}
    ProcTable DataTable {{ height: auto; max-height: {config.TABLE_MAX_HEIGHT}; }}
    """

    def __init__(self, monitor: Monitor, columns_for: Callable[[List[ProcInfo]], List[_Column]],
                 *, danger: bool = False):
        super().__init__()
        self.monitor = monitor
        self._columns_for = columns_for
        self.danger = danger
        self.table = DataTable(zebra_stripes=True, cursor_type="row",
                               cell_padding=config.TABLE_CELL_PADDING)
        self.selected: set = set()        # pids ticked for batch kill
        self._procs: List[ProcInfo] = []
        self._row_procs: List[ProcInfo] = []
        self._columns: List[_Column] = []
        self._anim_cells: List[Tuple[int, int, Dict[str, Any]]] = []
        self._sort_key: Optional[str] = None
        self._sort_reverse: bool = False
        self._frame: int = 0
        self._cursor_pid: Optional[int] = None   # row to re-select after refresh
        self._suspend_track = False
        self._empty_message = "— no processes —"
        self._row_style: Optional[Callable[[ProcInfo], Optional[str]]] = None
        self._extra_menu: Optional[Callable[[ProcInfo], List[Tuple]]] = None
        self._menu_extra: Dict[str, Tuple[List[ProcInfo], str]] = {}

    def compose(self):
        yield self.table

    def on_mount(self):
        self.watch(self.table, "hover_coordinate", self._hover_changed)

    # -- owner API ---------------------------------------------------------
    def set_danger(self, danger: bool):
        self.danger = danger

    def clear_selection(self):
        if self.selected:
            self.selected.clear()
            self._rebuild()

    def update(self, procs: List[ProcInfo], *, empty_message: str = "— no processes —",
               row_style: Optional[Callable[[ProcInfo], Optional[str]]] = None,
               extra_menu: Optional[Callable[[ProcInfo], List[Tuple]]] = None):
        self._procs = list(procs)
        self._empty_message = empty_message
        self._row_style = row_style
        self._extra_menu = extra_menu
        self._rebuild()

    def animate_progress(self, frame: int):
        self._frame = frame
        for row, col, data in self._anim_cells:
            try:
                self.table.update_cell_at(Coordinate(row, col), render_progress(data, frame), update_width=False)
            except Exception:
                pass

    # -- rendering ---------------------------------------------------------
    def _rebuild(self):
        table = self.table
        self._suspend_track = True
        table.clear(columns=True)
        self._anim_cells = []

        procs = list(self._procs)
        columns = self._columns_for(procs) or [COLS["PID"]]
        self._columns = columns

        if not procs:
            self._row_procs = []
            table.add_column("")
            table.add_row(Text(self._empty_message, style="dim"))
            self._suspend_track = False
            return

        sort_col = next((c for c in columns if c.key == self._sort_key and c.sort), None)
        if sort_col:
            procs.sort(key=sort_col.sort, reverse=self._sort_reverse)
        check = self.danger
        if check and self.selected:
            # Ticked rows float to the top so it's obvious what's selected.
            procs = ([p for p in procs if p.pid in self.selected]
                     + [p for p in procs if p.pid not in self.selected])
        self._row_procs = procs

        if check:
            table.add_column(Text("✓", justify="center"), width=CHECK_WIDTH)
        for col in columns:
            label = col.label
            if col.key == self._sort_key:
                label = label if col.width is None else label.center(col.width)
                label = Text(label, style="reverse" if self._sort_reverse else "reverse underline")
            table.add_column(label, width=col.width)

        offset = 1 if check else 0
        for r, proc in enumerate(procs):
            style = self._row_style(proc) if self._row_style else None
            row: List[Any] = []
            if check:
                ticked = proc.pid in self.selected
                row.append(Text("☑" if ticked else "☐", justify="center",
                                style="bold green" if ticked else "dim"))
            for ci, col in enumerate(columns):
                cell = _fit_cell(col.render(self, proc), col.width)
                if style:
                    cell = cell.copy() if isinstance(cell, Text) else Text(str(cell))
                    cell.stylize(style)
                row.append(cell)
                if col.meta_key is not None:
                    value = proc.meta.get(col.meta_key)
                    if value is not None and is_progress(value) and value.get("frac") is None:
                        self._anim_cells.append((r, offset + ci, value))
            table.add_row(*row)

        self._restore_cursor()
        self._suspend_track = False

    def _restore_cursor(self):
        if self._cursor_pid is None:
            return
        for i, proc in enumerate(self._row_procs):
            if proc.pid == self._cursor_pid:
                try:
                    self.table.move_cursor(row=i, animate=False)
                except Exception:
                    pass
                return

    # -- cursor tracking ---------------------------------------------------
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        if self._suspend_track:
            return
        r = event.cursor_row
        if 0 <= r < len(self._row_procs):
            self._cursor_pid = self._row_procs[r].pid

    # -- sorting -----------------------------------------------------------
    def on_data_table_header_selected(self, event: DataTable.HeaderSelected):
        event.stop()
        idx = event.column_index - (1 if self.danger else 0)
        if not (0 <= idx < len(self._columns)):
            return
        col = self._columns[idx]
        if col.sort is None:
            return
        if self._sort_key == col.key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key = col.key
            self._sort_reverse = col.key in DESC_FIRST_KEYS
        self._rebuild()

    # -- mouse: checkbox toggle + right-click menu -------------------------
    def on_mouse_down(self, event: events.MouseDown):
        coord = self.table.hover_coordinate
        valid_row = coord is not None and 0 <= coord.row < len(self._row_procs)
        # Left-click the checkbox column toggles selection (kill mode only).
        if event.button == 1 and self.danger and valid_row and coord.column == 0:
            proc = self._row_procs[coord.row]
            self.selected.discard(proc.pid) if proc.pid in self.selected else self.selected.add(proc.pid)
            self._cursor_pid = proc.pid
            event.stop(); event.prevent_default()
            self._rebuild()
            return
        if event.button != 3 or not valid_row:
            return
        event.stop(); event.prevent_default()
        proc = self._row_procs[coord.row]
        options: List[Tuple[str, str]] = [("copy", "📋 Copy info ")]
        self._menu_extra = {}
        if self.danger:
            options.append(("kill", f"💀 Kill (PID {proc.pid}) "))
            if self.selected:
                options.append(("kill_sel", f"💀 Kill {len(self.selected)} selected "))
            if self._extra_menu:
                for oid, label, procs, scope in self._extra_menu(proc):
                    self._menu_extra[oid] = (procs, scope)
                    options.append((oid, label))
        x = getattr(event, "screen_x", event.x)
        y = getattr(event, "screen_y", event.y)
        self.app.push_screen(ContextMenu(options, x, y),
                             lambda choice, p=proc: self._on_menu(choice, p))

    def _on_menu(self, choice: Optional[str], proc: ProcInfo):
        if choice == "copy":
            self._copy(proc)
        elif choice == "kill":
            self._confirm_kill([proc], f"PID {proc.pid}")
        elif choice == "kill_sel":
            procs = [p for p in self._row_procs if p.pid in self.selected]
            self._confirm_kill(procs, f"{len(procs)} selected")
        elif choice in self._menu_extra:
            procs, scope = self._menu_extra[choice]
            self._confirm_kill(procs, scope)

    # -- copy / kill -------------------------------------------------------
    def _clean_cell(self, col: _Column, proc: ProcInfo) -> str:
        if col.meta_key is not None:
            raw = proc.meta.get(col.meta_key)
            if raw is None:
                return ""
            return _progress_text(raw) if is_progress(raw) else str(raw)
        value = col.render(self, proc)
        return value.plain if isinstance(value, Text) else str(value)

    def _proc_info(self, proc: ProcInfo) -> str:
        return "\n".join(f"{col.label}: {self._clean_cell(col, proc)}".rstrip()
                         for col in self._columns)

    def _copy(self, proc: ProcInfo):
        try:
            self.app.copy_to_clipboard(self._proc_info(proc))
            self._notify(f"Copied info for PID {proc.pid}")
        except Exception as exc:
            self._notify(f"Copy failed: {exc}", error=True)

    def _confirm_kill(self, procs: List[ProcInfo], scope: str):
        if not procs:
            return
        if len(procs) == 1:
            msg = ("⚠ Kill this process?\nThis sends a terminate signal and cannot be undone.\n\n"
                   + self._proc_info(procs[0]))
            label = "Kill"
        else:
            listing = "\n".join(f"  • {p.pid:>7}  {_clip(p.cmd or p.pname, 60)}" for p in procs)
            msg = (f"⚠ Multi-process kill — {scope} ({len(procs)} processes).\n"
                   "ALL of them will be sent a terminate signal (cannot be undone):\n\n" + listing)
            label = f"Kill {len(procs)}"
        self.app.push_screen(ConfirmScreen(msg, confirm_label=label),
                             lambda ok, ps=list(procs): self._do_kill(ps) if ok else None)

    def _do_kill(self, procs: List[ProcInfo]):
        killed = failed = 0
        for proc in sorted(procs, key=lambda p: p.pid, reverse=True):
            try:
                psutil.Process(proc.pid).terminate()
                killed += 1
            except psutil.NoSuchProcess:
                killed += 1
            except Exception:
                failed += 1
            self.selected.discard(proc.pid)
        if failed:
            self._notify(f"Killed {killed}, failed {failed} (permission?)", error=True)
        else:
            self._notify(f"Sent terminate signal to {killed} process(es)")
        try:
            self.app.refresh_data()
        except Exception:
            pass

    def _hover_changed(self, coord: Optional[Coordinate]):
        table = self.table
        offset = 1 if self.danger else 0
        if coord is None or not (0 <= coord.row < len(self._row_procs)):
            table.tooltip = None
            return
        ci = coord.column - offset
        if not (0 <= ci < len(self._columns)):
            table.tooltip = None
            return
        table.tooltip = self._clean_cell(self._columns[ci], self._row_procs[coord.row]) or None

    def _notify(self, message: str, error: bool = False):
        try:
            self.app.notify(message, severity="error" if error else "information")
        except Exception:
            pass


class DeviceCard(Vertical):
    """Base card: a title, a one-line summary and a shared :class:`ProcTable`.

    Subclasses provide the header (title + stats, plus any extra widgets such as
    a GPU memory bar) and decide which columns / processes to show. The table
    itself and every interaction live in the reusable ProcTable.
    """

    DEFAULT_CSS = f"""
    DeviceCard {{
        height: auto;
        max-width: {config.CARD_MAX_WIDTH or '100%'};
        border: round $primary;
        border-title-color: $text;
        border-title-style: bold;
        padding: {config.CARD_PADDING[0]} {config.CARD_PADDING[1]};
        margin: 0;
    }}
    DeviceCard .stats {{ height: 1; }}
    DeviceCard .legend {{ height: auto; color: $text-muted; }}
    DeviceCard ProcTable {{ margin-top: 1; }}
    """

    def __init__(self, monitor: Monitor, danger: bool = False):
        super().__init__()
        self.monitor = monitor
        self.danger = danger
        self.stats = Static(classes="stats")
        self.proc_table = ProcTable(monitor, self._columns_for, danger=danger)
        self._device: Optional[DeviceInfo] = None
        self._deferred: Optional[DeviceInfo] = None

    def compose(self):
        yield self.stats
        yield from self._extra_widgets()
        yield self.proc_table

    def _extra_widgets(self):
        """Header widgets between the stats line and the table (GPU adds a bar)."""
        return iter(())

    def on_mount(self):
        if self._deferred is not None:
            self._apply(self._deferred)

    # -- updating ----------------------------------------------------------
    def update(self, device: DeviceInfo):
        if self.is_mounted:
            self._apply(device)
        else:
            self._deferred = device

    def _apply(self, device: DeviceInfo):
        self._deferred = None
        self._device = device
        self._render_header(device)
        self.proc_table.set_danger(self.danger)
        self.proc_table.update(self._visible_procs(device), empty_message=self._empty_message())

    def set_danger(self, danger: bool):
        self.danger = danger
        self.proc_table.set_danger(danger)
        if self._device is not None and self.is_mounted:
            self.proc_table.update(self._visible_procs(self._device), empty_message=self._empty_message())

    def animate_progress(self, frame: int):
        self.proc_table.animate_progress(frame)

    # -- subclass hooks ----------------------------------------------------
    def _render_header(self, device: DeviceInfo):
        raise NotImplementedError

    def _fixed_keys(self) -> Tuple[str, ...]:
        raise NotImplementedError

    def _visible_procs(self, device: DeviceInfo) -> List[ProcInfo]:
        raise NotImplementedError

    def _empty_message(self) -> str:
        raise NotImplementedError

    def _columns_for(self, procs: List[ProcInfo]) -> List[_Column]:
        return columns_with_meta(self._fixed_keys(), procs)


class GpuCard(DeviceCard):
    """One GPU: title, stats line, proportion bar, legend and process table."""

    def __init__(self, monitor: Monitor, zen: bool = False, danger: bool = False):
        super().__init__(monitor, danger=danger)
        self.bar = MemoryBar()
        self.legend = Static(classes="legend")
        self.zen = zen

    def _extra_widgets(self):
        yield self.bar
        yield self.legend

    def set_zen(self, zen: bool):
        if zen == self.zen:
            return
        self.zen = zen
        if self._device is not None and self.is_mounted:
            self._apply(self._device)

    def _render_header(self, device: DeviceInfo):
        assert isinstance(device, GPUInfo)
        gpu = device
        self.border_title = f" # {gpu.id}  <{gpu.name}> "
        self._update_stats(gpu)
        self._update_bar(gpu)
        self._update_legend(gpu)

    def _fixed_keys(self) -> Tuple[str, ...]:
        return ZEN_FIXED_KEYS

    def _visible_procs(self, device: DeviceInfo) -> List[ProcInfo]:
        """In zen mode the table is filtered down to the watched user."""
        if self.zen:
            return [p for p in device.procs if p.user == self.monitor.focus_user]
        return list(device.procs)

    # Normal mode shows every column; zen mode uses the metadata layout.
    def _columns_for(self, procs: List[ProcInfo]) -> List[_Column]:
        if self.zen:
            return columns_with_meta(self._fixed_keys(), procs)
        return NORMAL_COLUMNS or [COLS["PID"]]

    def _empty_message(self) -> str:
        if self.zen:
            return f"— no processes for [{self.monitor.focus_user}] —"
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
        line.append("[PROC] ", style="bold")
        line.append(f"{len(gpu.procs)}    ", style="bold")
        line.append("[USED] ", style="bold")
        line.append(f"{fmt_gb(gpu.mem_used)}", style="bold")
        line.append(f" / {fmt_gb(gpu.mem_total)} GB", style="dim")
        line.append(f" ({gpu.used_rate * 100:.0f}%)    ")
        line.append("[FREE] ", style="bold")
        line.append(f"{fmt_gb(gpu.mem_free)} GB ", style=free_style)
        if gpu.mem_util >= 0:
            line.append("   [⚡] ", style="bold")
            line.append(f"{gpu.mem_util}%", style=config.TEMP_COLOR)
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
        for user, mem in ordered:
            color = self.monitor.color_for(user)
            pct = mem / gpu.mem_total * 100 if gpu.mem_total else 0
            legend.append("🏆 " if user == mvp else "")
            if self.zen and user == self.monitor.focus_user:
                legend.append("★ ", style=color)
                legend.append(user, style=f"bold underline {color}")
            else:
                legend.append(f"● {user}", style=color)
            legend.append(f" {fmt_gb(mem)} GB ({pct:.0f}%)   ")
        self.legend.update(legend)


class CpuCard(DeviceCard):
    """The watched user's non-GPU, scopos-reporting processes (host-RAM view).

    Parallel to :class:`GpuCard` but with no GPU memory column and no bar/legend;
    it extends scopos to plain CPU jobs and to jobs not yet on a GPU. A job that
    later allocates GPU memory shows up under its GPU card automatically.
    """

    def _render_header(self, device: DeviceInfo):
        self.border_title = f" 🧮  <{device.name}>  ·  tracked process(es) of {self.monitor.focus_user} via scopos API "
        rss_total = sum(p.rss for p in device.procs)
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(f"[PROC] {len(device.procs)}", style="bold")
        line.append(f"    [RAM] {fmt_gb(rss_total)} GB", style="bold")
        self.stats.update(line)

    def _fixed_keys(self) -> Tuple[str, ...]:
        return CPU_FIXED_KEYS

    def _visible_procs(self, device: DeviceInfo) -> List[ProcInfo]:
        return list(device.procs)

    def _empty_message(self) -> str:
        return "— no scopos-reporting processes —"
