# -*- coding: utf-8 -*-
"""Pop-over dialogs for Scopos: a right-click context menu and a confirm box,
plus the shared "confirm then terminate" helper used by every kill path."""

from __future__ import annotations
import psutil
from typing import (Callable, List, Optional, Tuple)

from textual import events
from textual.app import ComposeResult
from textual.containers import (Horizontal, Vertical)
from textual.screen import ModalScreen
from textual.widgets import (Button, Label, OptionList)
from textual.widgets.option_list import Option


class ContextMenu(ModalScreen[Optional[str]]):
    """A small right-click menu placed at the cursor; returns the chosen id."""

    DEFAULT_CSS = """
    ContextMenu {
        background: transparent;
        align: left top;
    }
    ContextMenu OptionList {
        width: auto;
        max-width: 60;
        height: auto;
        max-height: 12;
        background: $panel;
        border: round $primary;
        padding: 0 1;
    }
    """

    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, options: List[Tuple[str, str]], x: int = 0, y: int = 0):
        super().__init__()
        self._options = options
        self._x = x
        self._y = y

    def compose(self) -> ComposeResult:
        yield OptionList(*[Option(label, id=oid) for oid, label in self._options])

    def on_mount(self) -> None:
        menu = self.query_one(OptionList)
        # Clamp so the menu stays fully on-screen near where the user clicked.
        w, h = self.app.size.width, self.app.size.height
        menu.styles.offset = (min(self._x, max(0, w - 30)), min(self._y, max(0, h - 8)))
        menu.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_close(self) -> None:
        self.dismiss(None)

    def on_click(self, event: events.Click) -> None:
        # A click that lands outside the menu (on the backdrop) closes it.
        menu = self.query_one(OptionList)
        try:
            inside = menu.region.contains(event.screen_x, event.screen_y)
        except Exception:
            inside = True
        if not inside:
            self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """A modal yes/no confirmation, returning ``True`` only on explicit confirm."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen #box {
        width: auto;
        max-width: 80;
        height: auto;
        padding: 1 2;
        background: $panel;
        border: thick $error;
    }
    ConfirmScreen #buttons {
        height: auto;
        align: center middle;
        padding-top: 1;
    }
    ConfirmScreen Button {
        margin: 0 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, message: str, confirm_label: str = "Kill"):
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label(self._message)
            with Horizontal(id="buttons"):
                yield Button(self._confirm_label, variant="error", id="ok")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self) -> None:
        # Default focus to Cancel so a stray Enter doesn't kill anything.
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")

    def action_cancel(self) -> None:
        self.dismiss(False)


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


# Seconds a process gets to exit after SIGTERM before we escalate to SIGKILL.
KILL_GRACE_SEC = 1.5


def terminate_procs(procs) -> Tuple[int, int, int]:
    """Actually terminate ``procs``; return ``(killed, survived, skipped)``.

    Robust against the three ways the old version got this wrong:

    * **PID reuse** — each ProcInfo carries ``create_ts``; before signalling we
      re-open the PID and bail if its creation time no longer matches, so we
      never hit a *different* process that recycled the PID.
    * **already gone** — a PID that no longer exists is *skipped*, not counted
      as a kill.
    * **SIGTERM-ignorers** (interactive shells, etc.) — we send SIGTERM, wait
      ``KILL_GRACE_SEC``, then SIGKILL whatever is still alive, and only report
      ``killed`` for processes that are genuinely gone afterwards.
    """
    targets = []
    skipped = 0
    for proc in procs:
        try:
            p = psutil.Process(proc.pid)
            if proc.create_ts and abs(p.create_time() - proc.create_ts) > 1.0:
                skipped += 1  # PID was reused by a different process — do NOT kill it
                continue
            targets.append(p)
        except psutil.NoSuchProcess:
            skipped += 1  # already gone
        except Exception:
            skipped += 1  # unreadable

    for p in targets:
        try:
            p.terminate()
        except Exception:
            pass
    _gone, alive = psutil.wait_procs(targets, timeout=KILL_GRACE_SEC)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    _gone2, still_alive = psutil.wait_procs(alive, timeout=KILL_GRACE_SEC)
    killed = len(targets) - len(still_alive)
    return killed, len(still_alive), skipped


def confirm_and_kill(app, procs, *, scope: str, detail: Optional[str] = None,
                     after: Optional[Callable] = None) -> None:
    """Confirm a (possibly multi-process) kill, then terminate on approval.

    Shared by the table right-click menu and the footer Kill button so the kill
    flow lives in exactly one place. ``detail`` is the full per-field info shown
    for a single process; multi-process kills always list every target. The
    termination itself runs in a thread worker (it waits for the grace period),
    so the UI never blocks.
    """
    procs = list(procs)
    if not procs:
        app.notify("Nothing to kill", timeout=2)
        return
    if len(procs) == 1 and detail:
        msg = "⚠ Kill this process?\nSIGTERM, then SIGKILL after a grace period. Cannot be undone.\n\n" + detail
        label = "Kill"
    else:
        listing = "\n".join(f"  • {p.pid:>7}  {_clip(p.cmd or p.pname, 60)}" for p in procs)
        msg = (f"⚠ Multi-process kill — {scope} ({len(procs)} processes).\n"
               "ALL of them will be terminated (SIGTERM, then SIGKILL). Cannot be undone:\n\n" + listing)
        label = f"Kill {len(procs)}"

    def report(killed: int, survived: int, skipped: int, targets):
        parts = []
        if killed:
            parts.append(f"killed {killed}")
        if survived:
            parts.append(f"{survived} survived (permission?)")
        if skipped:
            parts.append(f"{skipped} skipped (gone / PID reused)")
        app.notify("; ".join(parts) or "nothing to kill",
                   severity="error" if survived else "information")
        if after is not None:
            after(targets)

    def run_kill(targets):
        killed, survived, skipped = terminate_procs(targets)
        app.call_from_thread(report, killed, survived, skipped, targets)

    def on_confirm(ok: bool, targets=procs):
        if ok:
            app.run_worker(lambda: run_kill(targets), thread=True, group="kill", exclusive=False)

    app.push_screen(ConfirmScreen(msg, confirm_label=label), on_confirm)
