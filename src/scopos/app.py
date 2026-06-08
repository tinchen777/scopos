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

from textual.widgets import (ContentSwitcher, Tab, Tabs)

from . import config
from .monitor import (CPUInfo, GPUInfo, Monitor, DemoMonitor)
from .widgets.grid import (GpuCard, CpuCard)
from .widgets.others import (Clock, Logo, SysMeter)
from .widgets.views import (InfoView, TmuxView)

# The tabs / modes, in cycle order. The first three are layouts over the same
# device grid; "info" is a static page. ``info`` is treated as a mode here only
# so the tab bar and the data dispatch share one source of truth.
TABS = ("global", "zen", "tmux", "info")
TAB_LABELS = {"global": "Global", "zen": "Zen", "tmux": "Tmux", "info": "Info"}
VALID_MODES = TABS


def _view_for(mode: str) -> str:
    """Which ContentSwitcher panel a mode shows (global & zen share the grid)."""
    return {"global": "view-grid", "zen": "view-grid",
            "tmux": "view-tmux", "info": "view-info"}[mode]


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
    #tabs {{
        /* Textual 8: auto height may expand and starve the content switcher. */
        height: 3;
    }}
    #switcher {{
        height: 1fr;
    }}
    #switcher > VerticalScroll {{
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
        Binding("i", "info", show=False),
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
        self._tmux_view = TmuxView(self.monitor, danger=self.danger)
        self._info_view = InfoView(self.monitor)
        self._frame: int = 0
        self.theme = theme

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Logo()
            yield Static(id="spacer1")
            yield Clock()
            yield Static(id="spacer2")
            yield SysMeter(self.interval)
        yield Tabs(*(Tab(TAB_LABELS[m], id=f"tab-{m}") for m in TABS), id="tabs")
        with ContentSwitcher(initial=_view_for(self.mode), id="switcher"):
            with VerticalScroll(id="view-grid"):
                yield Container(id="grid")
            with VerticalScroll(id="view-tmux"):
                yield self._tmux_view
            with VerticalScroll(id="view-info"):
                yield self._info_view
        yield Static(id="status")
        yield Footer()

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
        if mode not in TABS:
            return
        self.mode = mode
        self.query_one("#switcher", ContentSwitcher).current = _view_for(mode)
        self.refresh_data()

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
        # The on-screen time reflects when this data was collected.
        try:
            self.query_one(Clock).show_time(time.time())
        except Exception:
            pass
        # Only refresh whatever the active tab is showing.
        if self.mode in ("global", "zen"):
            self._refresh_grid()
        elif self.mode == "tmux":
            self._refresh_tmux()
        elif self.mode == "info":
            self._refresh_info()

    def _refresh_grid(self):
        gpus, gpu_procs = self.monitor.collect_GPU()
        self._sync_gpu_cards(gpus)
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
        demo_tag = "demo" if self.demo else "live"
        self._set_status(
            f"{len(gpus)} GPU(s) · {len(gpu_procs) + n_cpu_procs} proc(s) · "
            f"{sum(len(g.user_mems) for g in gpus)} user(s)  ·  refresh {self.interval}s  ·  "
            f"{demo_tag}  ·  focus on [{self.monitor.focus_user}]"
        )

    def _refresh_tmux(self):
        sessions = self.monitor.collect_tmux()
        self._tmux_view.set_danger(self.danger)
        self._tmux_view.update(sessions)
        n_proc = sum(len(s.all_procs) for s in sessions)
        self._set_status(
            f"tmux · {len(sessions)} session(s) · {n_proc} proc(s)  ·  "
            f"focus on [{self.monitor.focus_user}]  ·  your own tmux server only"
        )

    def _refresh_info(self):
        self._info_view.update()
        self._set_status("info  ·  scopos & host overview")

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
        self.monitor.stop()
