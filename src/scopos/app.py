# -*- coding: utf-8 -*-
"""The Scopos Textual application."""

from __future__ import annotations
import os
import time
from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.app import (App, ComposeResult)
from textual.containers import (Container, Horizontal, VerticalScroll)
from textual.widgets import (Footer, Static, ContentSwitcher, Tab, Tabs)
from typing import (Dict, List, Optional)

from . import config
from .monitor import (CPUInfo, GPUInfo, Monitor, DemoMonitor)
from .widgets.grid import (GpuCard, CpuCard, ProcTable)
from .widgets.others import (Clock, Logo, SysMeter, CPUMeter)
from .widgets.views import (InfoView, TmuxView)

TABS_INFO = {
    "global": ("Global Mode (g)", "view-grid"),
    "zen": ("Zen Mode (z)", "view-grid"),
    "tmux": ("Tmux Mode (t)", "view-tmux"),
    "info": ("Info (i)", "view-info"),
}
TABS = list(TABS_INFO.keys())


class ScoposApp(App):
    """Monitor GPU memory usage, grouped by user."""

    TITLE = "SCOPOS"

    # Grid gutter / padding come from scopos.config so spacing can be tuned there.
    TOPBAR_CSS = """
    #topbar {
        height: 5;
        padding: 0 0;
        background: $panel;
    }
    #topbar #logo {
        width: auto;
        height: 5;
        padding-left: 1;
        content-align: left top;
    }
    #topbar #clock {
        width: auto;
        height: 4;
        content-align: center bottom;
    }
    #topbar #spacer1 {
        width: 1fr;
    }
    #topbar #spacer2 {
        width: 1fr;
    }
    #topbar #cpumeter {
        width: auto;
        height: 5;
        padding-right: 3;
        content-align: right bottom;
    }
    #topbar #sysmeter {
        width: auto;
        height: 4;
        padding-right: 2;
        content-align: right bottom;
    }
    """
    TAB_CSS = """
    #tabs {
        /* Textual 8: auto height may expand and starve the content switcher. */
        height: 2;
    }
    #tabs Tab {
        padding: 0 2;
        color: $text;
        background: $surface;
    }
    #tabs Tab:hover {
        background: $boost;
        color: green;
    }
    #tabs Tab.-active {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    #tabs Tab.-active:hover {
        background: $primary-lighten-1;
    }
    #switcher {
        height: 1fr;
    }
    #switcher > VerticalScroll {
        height: 1fr;
    }
    """
    GRID_CSS = f"""
    #grid {{
        layout: grid;
        grid-size: 2;
        grid-rows: auto;
        grid-gutter: {config.GRID_GUTTER[0]} {config.GRID_GUTTER[1]};
        height: auto;
        padding: {config.GRID_PADDING[0]} {config.GRID_PADDING[1]};
    }}
    """
    CSS = """
    Screen {
        layout: vertical;
    }
    #status {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    """ + TOPBAR_CSS + TAB_CSS + GRID_CSS

    # How often (seconds) indeterminate progress bars advance a frame.
    ANIM_INTERVAL = 0.25

    BINDINGS = [
        Binding("q,escape", "quit", "Quit", key_display="Q"),
        Binding("r", "refresh", "Refresh Now", key_display="R"),
        Binding("m", "mode", "Toggle Mode", key_display="M"),
        Binding("g", "global_mode", "Global Mode", show=False),
        Binding("z", "zen_mode", "Zen Mode", show=False),
        Binding("t", "tmux_mode", "Tmux Mode", show=False),
        Binding("i", "info", "Info", show=False),
        Binding("h", "toggle_dark", "Theme Light", key_display="H"),
        # Deliberately awkward so it isn't hit by accident: it arms right-click
        # process killing. Confirmed again per-kill by a dialog.
        Binding("d", "toggle_danger", "Danger mode", key_display="D"),
        Binding("k", "kill", "Kill", show=False, key_display="K"),
        Binding("c", "clear_ticks", "Clear ticks", show=False, key_display="C"),
    ]

    def __init__(self, focus_user: str, interval: int = 5, demo: bool = False, theme: str = "textual-dark", mode: str = "global"):
        super().__init__()
        if not focus_user:
            focus_user = os.environ.get("USER", "?")
        self.focus_user = focus_user.strip()
        self.interval = max(1, interval)
        self.demo = demo
        if demo:
            self.monitor = DemoMonitor(focus_user=self.focus_user)
        else:
            self.monitor = Monitor(focus_user=self.focus_user)
        self.mode = mode if mode in TABS else TABS[0]
        # When armed, the right-click menu offers "Kill"; off by default.
        self.danger = False

        self._gpu_cards: Dict[int, GpuCard] = {}
        self._cpu_card: Optional[CpuCard] = None
        self._frame: int = 0
        self.theme = theme

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Logo(id="logo")
            yield Static(id="spacer1")
            yield Clock(id="clock")
            yield Static(id="spacer2")
            yield CPUMeter(self.interval, id="cpumeter")
            yield SysMeter(self.interval, id="sysmeter")
        yield Tabs(*(Tab(i[0], id=f"tab-{m}") for m, i in TABS_INFO.items()), id="tabs")
        with ContentSwitcher(initial=TABS_INFO[self.mode][1], id="switcher"):
            with VerticalScroll(id="view-grid"):
                yield Container(id="grid")
            with VerticalScroll(id="view-tmux"):
                yield TmuxView(self.monitor, id="tmux", danger=self.danger)
            with VerticalScroll(id="view-info"):
                yield InfoView(self.monitor, id="info")
        yield Static(id="status")
        yield Footer(id="footer")

    def on_mount(self):
        # Selecting the tab fires TabActivated, which sets the view and refreshes.
        self.query_one(Tabs).active = f"tab-{self.mode}"
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
        cols = max(1, self.size.width // config.CARD_MIN_WIDTH)
        cols = min(cols, card_num)
        grid = self.query_one("#grid")
        grid.styles.grid_size_columns = cols

    # -- action ------------------------------------------------------------
    def _next_mode(self):
        index = (TABS.index(self.mode) + 1) % len(TABS)
        return TABS[index]

    def action_refresh(self):
        self.refresh_data()

    def _activate(self, mode: str):
        """Select a tab; the TabActivated handler does the real switching."""
        self.query_one(Tabs).active = f"tab-{mode}"

    def action_global_mode(self):
        self._activate("global")

    def action_zen_mode(self):
        self._activate("zen")

    def action_tmux_mode(self):
        self._activate("tmux")

    def action_info(self):
        self._activate("info")

    def action_mode(self):
        """Cycle global → zen → tmux → info."""
        self._activate(self._next_mode())

    def on_tabs_tab_activated(self, event: Tabs.TabActivated):
        mode = (event.tab.id or "").removeprefix("tab-")
        if mode in TABS:
            self.mode = mode
            self.query_one("#switcher", ContentSwitcher).current = TABS_INFO[mode][1]
            self.refresh_data()

    def action_clear_ticks(self):
        """Uncheck every batch-selected row across all tables."""
        cleared = 0
        for table in self.query(ProcTable):
            cleared += len(table.selected)
            table.clear_selection()
        if cleared:
            self.notify(f"Cleared {cleared} selection(s)", title="CLEAR", timeout=2)
        else:
            self.notify("No selection to clear", title="CLEAR", timeout=2)

    def action_toggle_danger(self):
        """Arm/disarm right-click process killing (still confirmed per-kill)."""
        self.danger = not self.danger
        self.refresh_data()
        self.notify(
            "Danger Mode ON\nRight-click a process to kill it (confirmation required)."
            if self.danger else "Danger Mode OFF",
            title="⚠ DANGER" if self.danger else "SAFE",
            severity="warning" if self.danger else "information",
            timeout=6,
        )

    # -- data --------------------------------------------------------------
    # Collection (NVML / psutil / tmux) is blocking, so it runs in a thread
    # worker and the result is applied back on the UI thread. ``exclusive`` +
    # a shared group means switching tabs cancels an in-flight collect, so the
    # UI never stalls (it just keeps the old data until the new data arrives).
    def refresh_data(self):
        if self.mode in ("global", "zen"):
            self._collect_grid()
        elif self.mode == "tmux":
            self._collect_tmux()
        elif self.mode == "info":
            # Cheap, main-thread-only psutil reads; no worker needed.
            self.query_one("#info", InfoView).update()
            self._set_status("info  ·  scopos & host overview")
            self._stamp_clock()

    @work(thread=True, exclusive=True, group="collect")
    def _collect_grid(self):
        mode = self.mode
        try:
            gpus, user_procs = self.monitor.collect_GPU()
            cpu = None
            if mode == "zen":
                cpu = self.monitor.collect_CPU({p.pid for p in user_procs.get(self.focus_user, [])})
        except Exception as exc:  # keep the UI alive on transient NVML errors
            self.call_from_thread(self._set_status, f"collection error: {exc}")
            return
        self.call_from_thread(self._apply_grid, mode, gpus, cpu)

    def _apply_grid(self, mode: str, gpus: List[GPUInfo], cpu: Optional[CPUInfo]):
        if self.mode != mode:  # user switched tabs while we were collecting
            return
        self._sync_gpu_cards(gpus)
        if mode == "zen" and cpu is not None:
            self._sync_cpu_card(cpu)
            n_cpu_procs = len(cpu.procs)
        else:
            if self._cpu_card is not None:
                self._cpu_card.remove()
                self._cpu_card = None
                self.call_after_refresh(self._relayout_columns)
            n_cpu_procs = 0
        demo_tag = "demo" if self.demo else "live"
        self._set_status(
            f"{len(gpus)} GPU(s) · {sum(len(gpu.procs) for gpu in gpus) + n_cpu_procs} proc(s) · "
            f"{sum(len(g.user_mems) for g in gpus)} user(s)  ·  refresh {self.interval}s  ·  "
            f"{demo_tag}  ·  focus on [{self.focus_user}]"
        )
        self._stamp_clock()

    @work(thread=True, exclusive=True, group="collect")
    def _collect_tmux(self):
        mode = self.mode
        try:
            sessions = self.monitor.collect_tmux()
        except Exception as exc:
            self.call_from_thread(self._set_status, f"collection error: {exc}")
            return
        self.call_from_thread(self._apply_tmux, mode, sessions)

    def _apply_tmux(self, mode: str, sessions: list):
        if self.mode != mode:
            return
        tmux = self.query_one("#tmux", TmuxView)
        tmux.set_danger(self.danger)
        tmux.update(sessions)
        n_proc = sum(len(s.all_procs) for s in sessions)
        self._set_status(
            f"tmux · {len(sessions)} session(s) · {n_proc} proc(s)  ·  "
            f"focus on [{self.focus_user}]  ·  your own tmux server only"
        )
        self._stamp_clock()

    def _stamp_clock(self):
        # The on-screen time reflects when this data was collected.
        try:
            self.query_one(Clock).show_time(time.time())
        except Exception:
            pass

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
        # Sync mode/danger and push the latest data to every card. A freshly
        # built card isn't mounted yet, so update() defers until on_mount.
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

    def _set_status(self, main: str):
        text = Text(
            f"{main}  ·  {self.mode}  ·  press m for {self._next_mode()} mode",
            style="dim",
        )
        if self.danger:
            text.append("  ·  ⚠ DANGER (right-click to kill)", style="bold red")
        self.query_one("#status", Static).update(text)

    def on_unmount(self):
        self.monitor.pn_stop()
