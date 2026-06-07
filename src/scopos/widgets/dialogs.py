# -*- coding: utf-8 -*-
"""Pop-over dialogs for Scopos: a right-click context menu and a confirm box."""

from __future__ import annotations
from typing import (List, Optional, Tuple)

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
