# -*- coding: utf-8 -*-
"""The reusable process table widget shared by the cards and the tmux view."""

from __future__ import annotations
from collections import Counter
from rich.text import Text
from textual import events
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable
from typing import (Any, Callable, Dict, List, Optional, Tuple)

from .. import config
from ..metadata.utils import is_progress
from ..monitor import (Monitor, ProcInfo)
from .columns import (
    CHECK_WIDTH, COLS, REVERSE_KEYS, Column, fit_cell, proc_full_info,
    progress_text, render_progress)
from .dialogs import (ContextMenu, confirm_and_kill)


class ProcTable(Vertical):
    """A process table: sorting, hover tooltips, a right-click copy/kill menu,
    cursor persistence across refreshes, and — in kill mode — batch selection
    via a leading checkbox column (ticked rows float to the top).

    The owner supplies a ``columns_for(procs) -> [Column]`` callback and feeds
    rows with :meth:`update`. This is the single home for all the table
    interaction shared by the GPU/CPU cards and the tmux page.
    """

    DEFAULT_CSS = f"""
    ProcTable {{ height: auto; }}
    ProcTable DataTable {{ height: auto; max-height: {config.TABLE_MAX_HEIGHT}; }}
    """

    def __init__(
        self,
        monitor: Monitor,
        columns_for: Callable[[List[ProcInfo]], List[Column]],
        *,
        danger: bool = False,
        initial_sort: Optional[Tuple[str, bool]] = None
    ):
        super().__init__()
        self.monitor = monitor
        self._columns_for = columns_for
        self.danger = danger
        # The device behind this table (e.g. the GPUInfo); style callables in
        # MODE_TUNING read it for things like "fraction of this GPU's memory".
        self.context: Any = None
        self.table = DataTable(
            zebra_stripes=True,
            cursor_type="row",
            cell_padding=config.TABLE_CELL_PADDING
        )
        self.selected: set = set()              # ticked pids (batch kill)
        self._sel_users: Dict[int, str] = {}    # ticked pid -> user (for the count-by-user readout)
        self._proc_by_pid: Dict[int, ProcInfo] = {}
        self._procs: List[ProcInfo] = []
        self._row_procs: List[ProcInfo] = []
        self._columns: List[Column] = []
        self._anim_cells: List[Tuple[int, int, Dict[str, Any]]] = []
        self._sort_key, self._sort_reverse = initial_sort if initial_sort else (None, False)
        self._frame: int = 0
        self._cursor_pid: Optional[int] = None  # row to re-select after refresh
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
            self._sel_users.clear()
            self._rebuild()
            self._notify_selection()

    def selected_by_user(self) -> Counter:
        return Counter(self._sel_users[pid] for pid in self.selected if pid in self._sel_users)

    def selected_procs(self) -> List[ProcInfo]:
        return [self._proc_by_pid[pid] for pid in self.selected if pid in self._proc_by_pid]

    def update(
        self,
        procs: List[ProcInfo],
        *,
        empty_message: str = "— no processes —",
        row_style: Optional[Callable[[ProcInfo], Optional[str]]] = None,
        extra_menu: Optional[Callable[[ProcInfo], List[Tuple]]] = None,
        context: Any = None
    ):
        self._procs = list(procs)
        self._empty_message = empty_message
        self._row_style = row_style
        self._extra_menu = extra_menu
        self.context = context
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
        self._proc_by_pid = {p.pid: p for p in procs}
        columns = self._columns_for(procs) or [COLS["PID"]]
        self._columns = columns

        if not procs:
            self._row_procs = []
            table.add_column("")
            table.add_row(Text(self._empty_message, style="dim"))
            self._suspend_track = False
            return

        sort_col = next((c for c in columns if c.key == self._sort_key and c.sort), None)
        if sort_col and sort_col.sort is not None:
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
                cell = fit_cell(col.render(self, proc), col.width)
                if style:
                    cell = cell.copy() if isinstance(cell, Text) else Text(str(cell))
                    cell.stylize(style)
                row.append(cell)
                if col.meta_key is not None:
                    value = proc.meta.get(col.meta_key)
                    if value is not None and is_progress(value):
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
            self._sort_reverse = col.key in REVERSE_KEYS
        self._rebuild()

    # -- mouse: checkbox toggle + right-click menu -------------------------
    def on_mouse_down(self, event: events.MouseDown):
        coord = self.table.hover_coordinate
        valid_row = coord is not None and 0 <= coord.row < len(self._row_procs)
        # Left-click the checkbox column toggles selection (kill mode only).
        if event.button == 1 and self.danger and valid_row and coord.column == 0:
            proc = self._row_procs[coord.row]
            if proc.pid in self.selected:
                self.selected.discard(proc.pid)
                self._sel_users.pop(proc.pid, None)
            else:
                self.selected.add(proc.pid)
                self._sel_users[proc.pid] = proc.user
            self._cursor_pid = proc.pid
            event.stop(); event.prevent_default()
            self._rebuild()
            self._notify_selection()
            return
        # Right-click anywhere on a valid row opens the context menu.
        if event.button != 3 or not valid_row:
            return
        event.stop(); event.prevent_default()
        proc = self._row_procs[coord.row]
        options: List[Tuple[str, str]] = [("copy", "📋 Copy info ")]
        self._menu_extra = {}
        if self.danger:
            options.append(("kill", f"💀 Kill [{proc.pid} - {proc.user}] "))
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
            self._confirm_kill(self.selected_procs(), f"{len(self.selected)} selected")
        elif choice in self._menu_extra:
            procs, scope = self._menu_extra[choice]
            self._confirm_kill(procs, scope)

    # -- copy / kill -------------------------------------------------------
    def _clean_cell(self, col: Column, proc: ProcInfo) -> str:
        """Plain text of one cell, for the hover tooltip."""
        if col.meta_key is not None:
            raw = proc.meta.get(col.meta_key)
            if raw is None:
                return ""
            return progress_text(raw) if is_progress(raw) else str(raw)
        value = col.render(self, proc)
        return value.plain if isinstance(value, Text) else str(value)

    # Copy and the kill confirmation use the COMPLETE record (every field plus
    # all reported metadata), independent of which columns this mode shows.
    def _copy(self, proc: ProcInfo):
        try:
            self.app.copy_to_clipboard(proc_full_info(proc))
            self.app.notify(f"Copied info for [{proc.pid} - {proc.user}]")
        except Exception as exc:
            self.app.notify(f"Copy failed: {exc}", severity="error")

    def _confirm_kill(self, procs: List[ProcInfo], scope: str):
        detail = proc_full_info(procs[0]) if len(procs) == 1 else None
        confirm_and_kill(self.app, procs, scope=scope, detail=detail, after=self._after_kill)

    def _after_kill(self, procs: List[ProcInfo]):
        for p in procs:
            self.selected.discard(p.pid)
            self._sel_users.pop(p.pid, None)
        self._notify_selection()
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

    def _notify_selection(self):
        """Tell the app a tick changed so it can refresh the status-bar count."""
        try:
            self.app.refresh_selection_status()
        except Exception:
            pass
