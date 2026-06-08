# -*- coding: utf-8 -*-
"""Full-page views for Scopos's tab bar: the tmux manager and the info page."""

from __future__ import annotations
import platform
import psutil
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import (Static, Tree)
from textual.widgets.tree import TreeNode
from typing import (List, Optional, Tuple)

from .. import (__version__, __author__)
from ..metadata.utils import is_progress
from ..monitor import (Monitor, ProcInfo, TmuxSession)
from .grid import (_fmt_gb, _progress_text)
from .dialogs import (ConfirmScreen, ContextMenu)


def _meta_summary(proc: ProcInfo) -> str:
    """A compact one-line summary of a process's reported metadata."""
    parts = []
    for key, value in proc.meta.items():
        if is_progress(value):
            parts.append(f"{key} {_progress_text(value)}")
        else:
            parts.append(f"{key}={value}")
    return "   ".join(parts)


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def proc_info_text(proc: ProcInfo) -> str:
    """Full, plain-text details of one process (for copy / single-kill dialogs)."""
    lines = [
        f"PID: {proc.pid}",
        f"USER: {proc.user}",
        f"SESSION: {proc.sname}",
        f"RUNTIME: {proc.runtime}",
        f"RAM: {_fmt_gb(proc.rss)} GB",
        f"COMMAND: {proc.cmd or proc.pname}",
    ]
    for key, value in proc.meta.items():
        shown = _progress_text(value) if is_progress(value) else value
        lines.append(f"{key.upper()}: {shown}")
    return "\n".join(lines)


class TmuxView(Vertical):
    """A tree of the focus user's tmux sessions → panes → processes.

    Right-click any node to copy its info; in danger mode the menu also offers
    Kill. Killing a pane or a whole session that contains several processes asks
    for confirmation and lists every process that would be terminated.
    """

    DEFAULT_CSS = """
    TmuxView { height: auto; }
    TmuxView Tree { height: auto; padding: 0 1; }
    TmuxView #hint { height: auto; color: $text-muted; padding: 0 2; }
    """

    def __init__(self, monitor: Monitor, danger: bool = False):
        super().__init__()
        self.monitor = monitor
        self.danger = danger
        self._tree: Tree = Tree("tmux")
        self._tree.show_root = False
        self._tree.guide_depth = 3
        self._hint = Static(id="hint")

    def compose(self) -> ComposeResult:
        yield self._hint
        yield self._tree

    def set_danger(self, danger: bool):
        self.danger = danger

    # -- rendering ---------------------------------------------------------
    def update(self, sessions: List[TmuxSession]):
        tree = self._tree
        tree.clear()
        total_procs = sum(len(s.all_procs) for s in sessions)
        self._hint.update(Text(
            f"tmux · {len(sessions)} session(s) · {total_procs} process(es)"
            f"  ·  right-click a node to copy"
            + ("  /  kill" if self.danger else "  ·  ctrl+shift+k to arm kill"),
            style="dim",
        ))
        if not sessions:
            tree.root.add_leaf(Text("— no tmux sessions for this user —", style="dim"))
            return
        focus = self.monitor.focus_user
        for session in sessions:
            tag = " ★" if session.attached else ""
            slabel = Text.assemble(
                ("⊞ ", "bold cyan"),
                (session.name, "bold"),
                (f"  ({len(session.all_procs)} proc)", "dim"),
                (tag, "yellow"),
            )
            snode = tree.root.add(slabel, data={"kind": "session", "obj": session}, expand=True)
            for pane in session.panes:
                shell = pane.procs[0] if pane.procs else None
                children = pane.procs[1:] if pane.procs else []
                shell_cmd = (shell.cmd or shell.pname) if shell else "?"
                plabel = Text.assemble(
                    ("▦ ", "green"),
                    (f"win{pane.window_idx}.{pane.pane_idx} ", "bold"),
                    (f"{pane.window_name} ", "magenta"),
                    (f"[{pane.pane_pid} {_clip(shell_cmd, 18)}]", "dim"),
                )
                pnode = snode.add(plabel, data={"kind": "pane", "obj": pane}, expand=True)
                if not children:
                    pnode.add_leaf(Text("· idle shell ·", style="dim"))
                for proc in children:
                    pnode.add_leaf(self._proc_label(proc, focus), data={"kind": "proc", "obj": proc})

    def _proc_label(self, proc: ProcInfo, focus: str) -> Text:
        color = self.monitor.color_for(proc.user)
        label = Text()
        label.append(f"{proc.pid:>7} ", style="dim")
        label.append(_clip(proc.cmd or proc.pname, 46), style=color if proc.user == focus else "")
        summary = _meta_summary(proc)
        if summary:
            label.append("  ")
            label.append(summary, style="cyan")
        label.append(f"   RAM {_fmt_gb(proc.rss)}G · {proc.runtime}", style="dim")
        return label

    # -- interaction -------------------------------------------------------
    def on_mouse_down(self, event: events.MouseDown):
        if event.button != 3:
            return
        line = self._tree.hover_line
        if line is None or line < 0:
            return
        node = self._tree.get_node_at_line(line)
        if node is None or not isinstance(node.data, dict):
            return
        event.stop()
        event.prevent_default()
        kind = node.data["kind"]
        options: List[Tuple[str, str]] = [("copy", "📋 Copy info")]
        if self.danger:
            label = {"proc": "💀 Kill process", "pane": "💀 Kill pane",
                     "session": "💀 Kill session"}[kind]
            options.append(("kill", label))
        x = getattr(event, "screen_x", event.x)
        y = getattr(event, "screen_y", event.y)
        self.app.push_screen(
            ContextMenu(options, x, y),
            lambda choice, n=node: self._on_menu(choice, n),
        )

    def _targets(self, node: TreeNode) -> List[ProcInfo]:
        kind, obj = node.data["kind"], node.data["obj"]
        if kind == "proc":
            return [obj]
        if kind == "pane":
            return list(obj.procs)
        if kind == "session":
            return list(obj.all_procs)
        return []

    def _on_menu(self, choice: Optional[str], node: TreeNode):
        if choice == "copy":
            self._copy(node)
        elif choice == "kill":
            self._confirm_kill(node)

    def _copy(self, node: TreeNode):
        procs = self._targets(node)
        if len(procs) == 1:
            text = proc_info_text(procs[0])
        else:
            kind, obj = node.data["kind"], node.data["obj"]
            scope = obj.name if kind == "session" else f"pane {getattr(obj, 'pane_pid', '')}"
            head = f"tmux {kind} {scope} · {len(procs)} process(es)"
            text = head + "\n" + "\n".join(f"  {p.pid:>7}  {p.cmd or p.pname}" for p in procs)
        try:
            self.app.copy_to_clipboard(text)
            self._notify(f"Copied {len(procs)} process(es)")
        except Exception as exc:
            self._notify(f"Copy failed: {exc}", error=True)

    def _confirm_kill(self, node: TreeNode):
        procs = self._targets(node)
        if not procs:
            return
        kind, obj = node.data["kind"], node.data["obj"]
        if len(procs) == 1:
            msg = (
                "⚠ Kill this process?\nThis sends a terminate signal and cannot be undone.\n\n"
                f"{proc_info_text(procs[0])}"
            )
        else:
            scope = f"session '{obj.name}'" if kind == "session" else "this pane"
            listing = "\n".join(f"  • {p.pid:>7}  {_clip(p.cmd or p.pname, 60)}" for p in procs)
            msg = (
                f"⚠ Multi-process kill — {scope} contains {len(procs)} processes.\n"
                "ALL of them will be sent a terminate signal (cannot be undone):\n\n"
                f"{listing}"
            )
        self.app.push_screen(
            ConfirmScreen(msg, confirm_label=f"Kill {len(procs)}" if len(procs) > 1 else "Kill"),
            lambda ok, ps=procs: self._do_kill(ps) if ok else None,
        )

    def _do_kill(self, procs: List[ProcInfo]):
        killed = failed = 0
        # Kill children before their pane shells so panes tear down cleanly.
        for proc in sorted(procs, key=lambda p: p.pid, reverse=True):
            try:
                psutil.Process(proc.pid).terminate()
                killed += 1
            except psutil.NoSuchProcess:
                killed += 1
            except Exception:
                failed += 1
        if failed:
            self._notify(f"Killed {killed}, failed {failed} (permission?)", error=True)
        else:
            self._notify(f"Sent terminate signal to {killed} process(es)")
        self.app.refresh_data()

    def _notify(self, message: str, error: bool = False):
        try:
            self.app.notify(message, severity="error" if error else "information")
        except Exception:
            pass


class InfoView(Vertical):
    """A static page: scopos version + this machine's basic specs."""

    DEFAULT_CSS = """
    InfoView { height: auto; padding: 1 3; }
    InfoView Static { height: auto; }
    """

    def __init__(self, monitor: Monitor):
        super().__init__()
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
