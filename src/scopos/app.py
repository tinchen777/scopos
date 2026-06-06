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
from .monitor import (GPUInfo, Monitor)
from .widgets import (GpuCard, Logo, SysMeter)


class Clock(Static):
    """Date / time / version, pinned top-right."""

    def __init__(self, interval: int):
        super().__init__()
        self.interval = interval

    def on_mount(self):
        self.update_clock()
        self.set_interval(self.interval, self.update_clock)

    def update_clock(self):
        now = time.localtime()
        text = Text(justify="left")
        text.append(time.strftime("%Y-%m-%d  ", now), style="bold")
        text.append(time.strftime("%H:%M:%S", now), style="bold cyan")
        self.update(text)


class ScoposApp(App):
    """Monitor GPU memory usage, grouped by user."""

    TITLE = "SCOPOS"

    # Roughly the narrowest a card stays readable; used to pick column count.
    # The full COMMAND column needs room, so cards stay wide and only tile into
    # multiple columns on genuinely wide terminals.
    CARD_MIN_WIDTH = 100

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
        height: 5;
        content-align: left top;
        background: $panel;
    }
    #topbar Clock {
        width: auto;
        height: 4;
        padding-bottom: 0;
        content-align: center bottom;
    }
    #topbar #spacer1 {
        width: 1fr;
    }
    #topbar #spacer2 {
            width: 1fr;
        }
    #topbar SysMeter {
        width: auto;
        height: 5;
        padding-right: 4;
        padding-bottom: 1;
        content-align: right bottom;
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

    # How often (seconds) indeterminate progress bars advance a frame.
    ANIM_INTERVAL = 0.25

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh now"),
        ("z", "toggle_zen", "Zen mode"),
        ("d", "toggle_dark", "Light/Dark"),
    ]

    def __init__(self, watch_user: str = "", interval: int = 5, demo: bool = False, theme: str = "ansi-dark", zen: bool = False):
        super().__init__()
        self.interval = max(1, interval)
        self.monitor = Monitor(watch_user=watch_user, demo=demo)
        self.zen = zen
        self._cards: Dict[int, GpuCard] = {}
        self._frame: int = 0
        self.theme = theme

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Logo()
            yield Static(id="spacer1")
            yield Clock(self.interval)
            yield Static(id="spacer2")
            yield SysMeter(self.monitor)
        with VerticalScroll(id="body"):
            yield Container(id="grid")
        yield Static(id="status")
        yield Footer()

    def on_mount(self):
        self.refresh_data()
        self.set_interval(self.interval, self.refresh_data)
        # A faster, lightweight tick that only animates indeterminate progress
        # bars (it updates just those cells, not the whole table).
        self.set_interval(self.ANIM_INTERVAL, self._progress_tick)

    def _progress_tick(self):
        if not self.zen:
            return
        self._frame += 1
        for card in self._cards.values():
            card.animate_progress(self._frame)

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

    def action_toggle_zen(self):
        """Switch between the normal layout and the focused 'zen' layout."""
        self.zen = not self.zen
        for card in self._cards.values():
            card.set_zen(self.zen)
        if self._cards:
            self._update_status(
                [c._gpu for c in self._cards.values() if c._gpu is not None]
            )

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
            card = GpuCard(self.monitor, zen=self.zen)
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
        layout = "zen" if self.zen else "normal"
        text = Text()
        text.append(f"{len(gpus)} GPU(s) · {n_proc} proc(s) · {len(users)} user(s)")
        text.append(
            f"  ·  refresh {self.interval}s  ·  {mode}  ·  {layout}{watch}"
            "  ·  press z for zen  ·  click a column header to sort",
            style="dim",
        )
        self.query_one("#status", Static).update(text)

    def on_unmount(self):
        self.monitor.stop()
