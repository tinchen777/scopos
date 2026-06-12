# -*- coding: utf-8 -*-
"""Full-page views for Scopos's tab bar: the tmux manager and the info page."""

from __future__ import annotations
import platform
import psutil
from dataclasses import replace
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static
from typing import (Dict, List, Tuple)

from .. import (__version__, __author__)
from ..monitor import (Monitor, ProcInfo, TmuxPane, TmuxSession)
from .columns import (TMUX_COLUMNS, columns_with_meta)
from .proc_table import ProcTable


class TmuxView(Vertical):
    """The focus user's tmux processes as one flat, grid-style table.

    It reuses :class:`~scopos.widgets.grid.ProcTable`, so sorting, hover
    tooltips, the right-click copy/kill menu and batch selection all behave
    exactly like the GPU/CPU cards. The pane *shell* rows (idle zsh) are dimmed
    so the programs actually running stand out; right-clicking a row can also
    kill its whole pane or session (with a multi-process confirmation).
    """

    DEFAULT_CSS = """
    TmuxView { height: auto; }
    TmuxView #hint { height: auto; color: $text-muted; padding: 0 2; }
    """

    def __init__(self, monitor: Monitor, id: str, danger: bool = False):
        super().__init__(id=id)
        self.monitor = monitor
        self.danger = danger
        self._hint = Static(id="hint")
        # Default sort on first entering tmux mode: by COMMAND with the actual
        # programs above the idle pane shells (see _columns_for).
        self._table = ProcTable(monitor, self._columns_for, danger=danger,
                                initial_sort=("COMMAND", False))
        self._shell_pids: set = set()
        self._pane_of: Dict[int, TmuxPane] = {}
        self._session_of: Dict[int, TmuxSession] = {}

    def compose(self) -> ComposeResult:
        yield self._hint
        yield self._table

    def set_danger(self, danger: bool):
        self.danger = danger
        self._table.set_danger(danger)

    def animate_progress(self, frame: int):
        self._table.animate_progress(frame)

    def _columns_for(self, procs: List[ProcInfo]) -> list:
        cols = columns_with_meta(TMUX_COLUMNS, procs, mode="tmux")
        # Sorting by COMMAND in tmux groups the programs first, shells last
        # (then alphabetical), so the running work is always on top.
        return [
            replace(c, sort=lambda p: (p.pid in self._shell_pids, p.cmd.lower()))
            if c.key == "COMMAND" else c
            for c in cols
        ]

    def update(self, named_sessions: Dict[str, TmuxSession], all_procs: List[ProcInfo]):
        self._shell_pids.clear()
        self._pane_of.clear()
        self._session_of.clear()
        for session in named_sessions.values():
            for pane in session.panes:
                for i, proc in enumerate(pane.procs):
                    self._pane_of[proc.pid] = pane
                    self._session_of[proc.pid] = session
                    if i == 0:
                        self._shell_pids.add(proc.pid)  # the pane's shell
        n_prog = len(all_procs) - len(self._shell_pids)
        hint = (f"tmux · {len(named_sessions)} session(s) · {n_prog} program(s) · "
                f"{len(self._shell_pids)} shell(s)")
        self._hint.update(Text(hint, style="dim"))
        self._table.set_danger(self.danger)
        self._table.update(
            all_procs,
            empty_message="— no tmux sessions for this user —",
            row_style=lambda p: "dim" if p.pid in self._shell_pids else None,
            extra_menu=self._extra_menu,
        )

    def _extra_menu(self, proc: ProcInfo) -> List[Tuple]:
        """Right-click extras for tmux: kill the whole pane / session."""
        options: List[Tuple] = []
        pane = self._pane_of.get(proc.pid)
        session = self._session_of.get(proc.pid)
        if pane is not None and len(pane.procs) > 1:
            options.append(("kill_pane", f"💀 Kill pane {pane.alias} ", list(pane.procs), f"pane {pane.alias}"))
        if session is not None:
            session_procs = session.session_procs
            if len(session_procs) > 1:
                options.append((
                    "kill_session", f"💀 Kill session {session.alias} ",
                    list(session_procs), f"session {session.alias}"
                ))
        return options


class InfoView(Vertical):
    """A static page: scopos version + this machine's basic specs."""

    DEFAULT_CSS = """
    InfoView { height: auto; padding: 1 3; }
    InfoView Static { height: auto; }
    """

    def __init__(self, monitor: Monitor, id: str):
        super().__init__(id=id)
        self.monitor = monitor
        self._body = Static()

    def compose(self) -> ComposeResult:
        yield self._body

    def update(self):
        gb = 1024 ** 3
        vm = psutil.virtual_memory()
        text = Text()
        text.append("SCOPOS\n", style="bold cyan")
        text.append(f"  version   {__version__}\n", style="dim")
        text.append(f"  author    {__author__}\n", style="dim")
        text.append(f"  focus     {self.monitor.focus_user or '-'}\n\n", style="dim")

        text.append("HOST\n", style="bold cyan")
        try:
            text.append(f"  hostname  {platform.node()}\n")
            text.append(f"  os        {platform.platform()}\n")
            text.append(f"  python    {platform.python_version()}\n")
            text.append(f"  cpu       {psutil.cpu_count(logical=True)} threads "
                        f"({psutil.cpu_count(logical=False)} cores)\n")
            text.append(f"  memory    {vm.used / gb:.1f} / {vm.total / gb:.1f} GB"
                        f"  ({vm.percent:.0f}%)\n\n")
        except Exception as exc:
            text.append(f"  (host info unavailable: {exc})\n\n", style="red")

        specs = self.monitor.gpu_specs()
        text.append(f"GPUs ({len(specs)})\n", style="bold cyan")
        if not specs:
            text.append("  none detected\n", style="dim")
        for gid, name, total in specs:
            text.append(f"  #{gid}  ", style="bold")
            text.append(f"{name}", style="green")
            text.append(f"   {total / gb:.0f} GB\n", style="dim")
        self._body.update(text)
