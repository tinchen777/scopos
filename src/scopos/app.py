# -*- coding: utf-8 -*-
"""The Scopos Textual application."""

from __future__ import annotations
import time
from rich.text import Text
from textual.binding import Binding
from textual.app import (App, ComposeResult)
from textual.containers import (Container, Horizontal, VerticalScroll)
from textual.widgets import (Footer, Static)
from typing import (Dict, List, Optional)

from . import config
from .monitor import (CPUInfo, GPUInfo, Monitor, DemoMonitor)
from .widgets.grid import (GpuCard, CpuCard)
from .widgets.others import (Clock, Logo, SysMeter)

VALID_MODES = ("global", "zen", "tmux")


class ScoposApp(App):
    """Monitor GPU memory usage, grouped by user."""

    TITLE = "SCOPOS"

    # Roughly the narrowest a card stays readable; used to pick column count.
    # The full COMMAND column needs room, so cards stay wide and only tile into
    # multiple columns on genuinely wide terminals. (Tunable in scopos.config.)
    CARD_MIN_WIDTH = config.CARD_MIN_WIDTH

    # Grid gutter / padding come from scopos.config so spacing can be tuned there.
    CSS = f"""
    Screen {{
        layout: vertical;
    }}
    #topbar {{
        height: 5;
        padding: 0 1;
        background: $panel;
    }}
    #topbar Logo {{
        width: auto;
        height: 5;
        content-align: left top;
        background: $panel;
    }}
    #topbar Clock {{
        width: auto;
        height: 4;
        padding-bottom: 0;
        content-align: center bottom;
    }}
    #topbar #spacer1 {{
        width: 1fr;
    }}
    #topbar #spacer2 {{
            width: 1fr;
        }}
    #topbar SysMeter {{
        width: auto;
        height: 5;
        padding-right: 4;
        padding-bottom: 1;
        content-align: right bottom;
    }}
    #grid {{
        layout: grid;
        grid-size: 1;
        grid-rows: auto;
        grid-gutter: {config.GRID_GUTTER[0]} {config.GRID_GUTTER[1]};
        height: auto;
        padding: {config.GRID_PADDING[0]} {config.GRID_PADDING[1]};
    }}
    #body {{
        height: 1fr;
    }}
    #status {{
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }}
    """

    # How often (seconds) indeterminate progress bars advance a frame.
    ANIM_INTERVAL = 0.25

    BINDINGS = [
        ("q,escape", "quit", "Quit"),
        ("r", "refresh", "Refresh now"),
        ("m", "mode", "Toggle mode"),
        Binding("g", "global_mode", show=False),
        Binding("z", "zen_mode", show=False),
        Binding("t", "tmux_mode", show=False),
        ("d", "toggle_dark", "Light/Dark"),
        # Deliberately awkward so it isn't hit by accident: it arms right-click
        # process killing. Confirmed again per-kill by a dialog.
        ("ctrl+shift+k", "toggle_danger", "Danger/Kill mode"),
    ]

    def __init__(self, focus_user: str = "", interval: int = 5, demo: bool = False, theme: str = "ansi-dark", mode: str = "global"):
        super().__init__()
        self.interval = max(1, interval)
        self.demo = demo
        if demo:
            self.monitor = DemoMonitor(focus_user=focus_user)
        else:
            self.monitor = Monitor(focus_user=focus_user)
        self.mode = mode if mode in VALID_MODES else "global"
        # When armed, the right-click menu offers "Kill"; off by default.
        self.danger = False

        self._gpu_cards: Dict[int, GpuCard] = {}
        self._cpu_card: Optional[CpuCard] = None
        self._frame: int = 0
        self.theme = theme

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Logo()
            yield Static(id="spacer1")
            yield Clock()
            yield Static(id="spacer2")
            yield SysMeter(self.interval)
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
        if self.mode != "zen":
            return
        self._frame += 1
        for gpu_card in self._gpu_cards.values():
            gpu_card.animate_progress(self._frame)
        if self._cpu_card is not None:
            self._cpu_card.animate_progress(self._frame)

    def on_resize(self):
        self._relayout_columns()
        # The host meter shrinks its bars/text to fit the new width.
        try:
            self.query_one(SysMeter).refresh_stats()
        except Exception:
            pass

    # -- layout ------------------------------------------------------------
    def _relayout_columns(self):
        if not self._gpu_cards and self._cpu_card is None:
            return
        card_num = len(self._gpu_cards) + (1 if self._cpu_card else 0)
        cols = max(1, self.size.width // self.CARD_MIN_WIDTH)
        cols = min(cols, card_num)
        grid = self.query_one("#grid")
        grid.styles.grid_size_columns = cols

    # -- action ------------------------------------------------------------
    def _next_mode(self):
        index = (VALID_MODES.index(self.mode) + 1) % len(VALID_MODES)
        return VALID_MODES[index]

    def action_refresh(self):
        self.refresh_data()

    def _toggle_mode(self, mode: str):
        self.mode = mode
        self.refresh_data()
        self.notify(
            f"Switch to {mode.capitalize()} mode",
            title=mode.upper(),
            severity="information",
            timeout=3,
        )

    def action_global_mode(self):
        """Switch to the `global` layout."""
        self._toggle_mode("global")

    def action_zen_mode(self):
        """Switch to the `zen` layout."""
        self._toggle_mode("zen")

    def action_tmux_mode(self):
        """Switch to the `tmux` layout."""
        self._toggle_mode("tmux")

    def action_mode(self):
        """Switch between `global`, `zen`, and `tmux` layouts."""
        self._toggle_mode(self._next_mode())

    def action_toggle_danger(self):
        """Arm/disarm right-click process killing (still confirmed per-kill)."""
        self.danger = not self.danger
        self.refresh_data()
        self.notify(
            "DANGER mode ON\n — right-click a process to Kill it (you'll be asked to confirm)"
            if self.danger else "DANGER mode OFF",
            title="⚠ DANGER" if self.danger else "Safe",
            severity="warning" if self.danger else "information",
            timeout=6,
        )

    # -- data --------------------------------------------------------------
    def refresh_data(self):
        # refresh time
        try:
            self.query_one(Clock).show_time(time.time())
        except Exception:
            pass
        # refresh gpus
        gpus, gpu_procs = self.monitor.collect_GPU()
        self._sync_gpu_cards(gpus)
        # refresh cpu
        if self.mode == "zen":
            cpu = self.monitor.collect_CPU({p.pid for p in gpu_procs})
            self._sync_cpu_card(cpu)
            n_cpu_procs = len(cpu.procs)
        else:
            if self._cpu_card is not None:
                self._cpu_card.remove()
                self._cpu_card = None
                self.call_after_refresh(self._relayout_columns)
            n_cpu_procs = 0
        # refresh status
        self._update_status(
            n_procs=len(gpu_procs) + n_cpu_procs,
            n_users=sum(len(g.user_mems) for g in gpus),
            n_gpus=len(gpus)
        )

    def _sync_gpu_cards(self, gpus: List[GPUInfo]):
        wanted = {g.id for g in gpus}
        if wanted != set(self._gpu_cards):
            # GPU set changed (first run, or hot-plug): rebuild the grid. This also
            # drops the cpu card, which _sync_cpu_card re-creates afterwards.
            grid = self.query_one("#grid")
            grid.remove_children()
            self._gpu_cards.clear()
            self._cpu_card = None
            for gpu in gpus:
                gpu_card = GpuCard(self.monitor, zen=self.mode == "zen", danger=self.danger)
                self._gpu_cards[gpu.id] = gpu_card
                grid.mount(gpu_card)
            self.call_after_refresh(self._relayout_columns)
        else:
            # GPU set is the same, just update the existing cards in-place for smoothness.
            for card in self._gpu_cards.values():
                card.set_zen(self.mode == "zen")
                card.set_danger(self.danger)
            for gpu in gpus:
                self._gpu_cards[gpu.id].update(gpu)

    def _sync_cpu_card(self, cpu: CPUInfo):
        """Keep the CPU card resident in zen mode (for the watched user).

        It lists every process of the watched user that reports to scopos but
        isn't currently on a GPU, and stays on screen even when empty so the
        host-memory view is always available in zen mode.
        """
        if self._cpu_card is None:
            self._cpu_card = CpuCard(self.monitor, danger=self.danger)
            grid = self.query_one("#grid")
            if self._gpu_cards:
                grid.mount(self._cpu_card, before=next(iter(self._gpu_cards.values())))
            else:
                grid.mount(self._cpu_card)
            self.call_after_refresh(self._relayout_columns)
        self._cpu_card.set_danger(self.danger)
        self._cpu_card.update(cpu)

    def _update_status(self, n_procs: int, n_users: int, n_gpus: int):
        demo_tag = "demo" if self.demo else "live"
        focus = f"focus on [{self.monitor.focus_user}]"
        text = Text(
            f"{n_gpus} GPU(s) · {n_procs} proc(s) · {n_users} user(s)"
            f"  ·  refresh {self.interval}s  ·  {demo_tag}  ·  {focus}"
            f"  ·  {self.mode}  ·  press m switch to {self._next_mode()} mode",
            style="dim",
        )
        if self.danger:
            text.append("  ·  ⚠ DANGER (right-click to kill)", style="bold red")
        self.query_one("#status", Static).update(text)

    def on_unmount(self):
        self.monitor.stop()
