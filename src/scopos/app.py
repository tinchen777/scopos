# -*- coding: utf-8 -*-
"""The Scopos Textual application."""

from __future__ import annotations
import time
from rich.text import Text
from textual.app import (App, ComposeResult)
from textual.containers import (Container, Horizontal, VerticalScroll)
from textual.widgets import (Footer, Static)
from typing import (Dict, List)

from . import __version__
from .monitor import GPUInfo, Monitor
from .widgets import GpuCard, Logo, SysMeter


class Clock(Static):
    """Date / time / version, pinned top-right."""

    def on_mount(self):
        self.update_clock()
        self.set_interval(1.0, self.update_clock)

    def update_clock(self):
        now = time.localtime()
        text = Text(justify="right")
        text.append(time.strftime("%Y-%m-%d\n", now), style="bold")
        text.append(time.strftime("%H:%M:%S\n", now), style="bold cyan")
        text.append(f"scopos {__version__}", style="dim")
        self.update(text)


class ScoposApp(App):
    """Monitor GPU memory usage, grouped by user."""

    TITLE = "SCOPOS"

    # Roughly the narrowest a card stays readable; used to pick column count.
    CARD_MIN_WIDTH = 60

    CSS = """
    Screen {
        layout: vertical;
    }
    #topbar {
        height: 5;
        padding: 0 1;
        background: $panel;
    }
    #topbar Logo {
        width: auto;
        content-align: left top;
    }
    #topbar #spacer {
        width: 1fr;
    }
    #topbar Clock {
        width: auto;
        content-align: right top;
    }
    #grid {
        layout: grid;
        grid-size: 1;
        grid-rows: auto;
        grid-gutter: 1 2;
        height: auto;
        padding: 1 2;
    }
    #body {
        height: 1fr;
    }
    #status {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh now"),
        ("d", "toggle_dark", "Light/Dark"),
    ]

    def __init__(self, watch_user: str = "", interval: int = 5, demo: bool = False):
        super().__init__()
        self.interval = max(1, interval)
        self.monitor = Monitor(watch_user=watch_user, demo=demo)
        self.show_detail = bool(self.monitor.watch_user)
        self._cards: Dict[int, GpuCard] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Logo()
            yield SysMeter(self.monitor)
            yield Static(id="spacer")
            yield Clock()
        with VerticalScroll(id="body"):
            yield Container(id="grid")
        yield Static(id="status")
        yield Footer()

    def on_mount(self):
        self.refresh_data()
        self.set_interval(self.interval, self.refresh_data)

    def on_resize(self):
        self._relayout_columns()

    # -- layout ------------------------------------------------------------
    def _relayout_columns(self):
        if not self._cards:
            return
        width = self.size.width
        cols = max(1, width // self.CARD_MIN_WIDTH)
        cols = min(cols, len(self._cards))
        grid = self.query_one("#grid")
        grid.styles.grid_size_columns = cols

    # -- data --------------------------------------------------------------
    def action_refresh(self):
        self.refresh_data()

    def refresh_data(self):
        try:
            gpus = self.monitor.collect()
        except Exception as exc:  # keep the UI alive on transient NVML errors
            self.query_one("#status", Static).update(
                Text(f"collection error: {exc}", style="red")
            )
            return
        self._sync_cards(gpus)
        for gpu in gpus:
            self._cards[gpu.index].update(gpu)
        self._update_status(gpus)

    def _sync_cards(self, gpus: List[GPUInfo]):
        wanted = {g.index for g in gpus}
        if wanted == set(self._cards):
            return
        # GPU set changed (first run, or hot-plug): rebuild the grid.
        grid = self.query_one("#grid")
        grid.remove_children()
        self._cards.clear()
        for gpu in gpus:
            card = GpuCard(self.monitor, self.show_detail)
            self._cards[gpu.index] = card
            grid.mount(card)
        self.call_after_refresh(self._relayout_columns)

    def _update_status(self, gpus: List[GPUInfo]):
        n_proc = sum(len(g.procs) for g in gpus)
        users = {p.user for g in gpus for p in g.procs}
        mode = "demo" if self.monitor.demo else "live"
        watch = (
            f"  ·  watching [{self.monitor.watch_user}]"
            if self.monitor.watch_user
            else ""
        )
        text = Text()
        text.append(f"{len(gpus)} GPU · {n_proc} proc · {len(users)} users")
        text.append(
            f"  ·  refresh {self.interval}s  ·  {mode}{watch}"
            "  ·  click a column header to sort",
            style="dim",
        )
        self.query_one("#status", Static).update(text)

    def on_unmount(self):
        self.monitor.stop()
