# -*- coding: utf-8 -*-
"""Full-page views for Scopos's tab bar: the tmux manager and the info page."""

from __future__ import annotations
import platform
import psutil
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static
from typing import (Dict, List, Tuple)

from .. import (__version__, __author__)
from ..monitor import (Monitor, ProcInfo, TmuxPane, TmuxSession)
from .columns import columns_with_meta
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

    # SESSION shows "name:win.pane", so it doubles as the grouping column.
    FIXED_KEYS: Tuple[str, ...] = ("SESSION", "PID", "RAM/GB", "RUNTIME")

    def __init__(self, monitor: Monitor, id: str, danger: bool = False):
        super().__init__(id=id)
        self.monitor = monitor
        self.danger = danger
        self._hint = Static(id="hint")
        self._table = ProcTable(monitor, self._columns_for, danger=danger)
        self._shell_pids: set = set()
        self._pane_of: Dict[int, TmuxPane] = {}
        self._session_of: Dict[int, TmuxSession] = {}

    def compose(self) -> ComposeResult:
        yield self._hint
        yield self._table

    def set_danger(self, danger: bool):
        self.danger = danger
        self._table.set_danger(danger)

    def _columns_for(self, procs: List[ProcInfo]) -> list:
        return columns_with_meta(self.FIXED_KEYS, procs)

    def update(self, sessions: List[TmuxSession]):
        self._shell_pids.clear()
        self._pane_of.clear()
        self._session_of.clear()
        procs: List[ProcInfo] = []
        for session in sessions:
            for pane in session.panes:
                for i, proc in enumerate(pane.procs):
                    self._pane_of[proc.pid] = pane
                    self._session_of[proc.pid] = session
                    if i == 0:
                        self._shell_pids.add(proc.pid)  # the pane's shell
                procs.extend(pane.procs)
        n_prog = len(procs) - len(self._shell_pids)
        hint = (f"tmux · {len(sessions)} session(s) · {n_prog} program(s) · "
                f"{len(self._shell_pids)} shell(s)")
        hint += ("   ·   right-click: copy / kill (proc · pane · session)" if self.danger
                 else "   ·   right-click to copy · ctrl+shift+k to arm kill")
        self._hint.update(Text(hint, style="dim"))
        self._table.set_danger(self.danger)
        self._table.update(
            procs,
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
            loc = f"{pane.session}:{pane.window_idx}.{pane.pane_idx}"
            options.append(("kill_pane", f"💀 Kill pane {loc} ", list(pane.procs), f"pane {loc}"))
        if session is not None and len(session.all_procs) > 1:
            options.append(("kill_session", f"💀 Kill session {session.name} ",
                            list(session.all_procs), f"session {session.name}"))
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
