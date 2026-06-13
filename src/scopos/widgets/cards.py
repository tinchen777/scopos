# -*- coding: utf-8 -*-
"""Per-device cards: a header (+ a GPU bar/legend) above a shared ProcTable."""

from __future__ import annotations
from rich.text import Text
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static
from typing import (List, Optional, Tuple)

from .. import config
from ..monitor import (DeviceInfo, GPUInfo, Monitor, ProcInfo)
from ._utils import fmt_gb
from .columns import (
    COLS, GLOBAL_COLUMNS, ZEN_COLUMNS, CPU_COLUMNS, Column, columns_with_meta)
from .proc_table import ProcTable


class MemoryBar(Widget):
    """A single-line bar whose coloured segments show each user's share.

    The bar fills its own width, so it grows/shrinks with the terminal — that's
    what gives the at-a-glance proportion view.
    """

    DEFAULT_CSS = """
    MemoryBar { height: 1; width: 1fr; }
    """

    def __init__(self):
        super().__init__()
        self._segments: List[Tuple[str, float]] = []
        self._total: float = 1.0

    def set_data(self, segments: List[Tuple[str, float]], total: float):
        """segments: list of (colour, weight); total: the bar's full weight."""
        self._segments = segments
        self._total = total or 1.0
        self.refresh()

    def render(self) -> Text:
        width = self.size.width or 1
        text = Text(no_wrap=True, overflow="crop")
        used = 0
        for color, weight in self._segments:
            if weight <= 0:
                continue
            cells = round(weight / self._total * width)
            if cells == 0:
                cells = 1  # keep tiny-but-present users visible
            cells = min(cells, width - used)
            if cells <= 0:
                break
            text.append("█" * cells, style=color)
            used += cells
        if used < width:
            text.append("░" * (width - used), style=config.BAR_TRACK_COLOR)
        return text


class DeviceCard(Vertical):
    """Base card: a title, a one-line summary and a shared :class:`ProcTable`.

    Subclasses provide the header (title + stats, plus any extra widgets such as
    a GPU memory bar) and decide which columns / processes to show.
    """

    DEFAULT_CSS = f"""
    DeviceCard {{
        height: auto;
        max-width: {config.CARD_MAX_WIDTH or '100%'};
        border: round $primary;
        border-title-color: $text;
        border-title-style: bold;
        padding: {config.CARD_PADDING[0]} {config.CARD_PADDING[1]};
        margin: 0;
    }}
    DeviceCard .stats {{ height: 1; }}
    DeviceCard .legend {{ height: auto; color: $text-muted; }}
    DeviceCard ProcTable {{ margin-top: 1; }}
    """

    def __init__(self, monitor: Monitor, danger: bool = False):
        super().__init__()
        self.monitor = monitor
        self.danger = danger
        self.stats = Static(classes="stats")
        self.proc_table = ProcTable(monitor, self._columns_for, danger=danger)
        self._device: Optional[DeviceInfo] = None
        self._deferred: Optional[DeviceInfo] = None

    def on_mount(self):
        if self._deferred is not None:
            self._apply(self._deferred)

    # -- updating ----------------------------------------------------------
    def update(self, device: DeviceInfo):
        if self.is_mounted:
            self._apply(device)
        else:
            self._deferred = device

    def _apply(self, device: DeviceInfo):
        self._deferred = None
        self._device = device
        self._render_header(device)
        self.proc_table.set_danger(self.danger)
        self.proc_table.update(self._visible_procs(device), empty_message=self._empty_message(),
                               context=device)

    def set_danger(self, danger: bool):
        self.danger = danger
        self.proc_table.set_danger(danger)
        if self._device is not None and self.is_mounted:
            self.proc_table.update(self._visible_procs(self._device), empty_message=self._empty_message(),
                                   context=self._device)

    # -- subclass hooks ----------------------------------------------------
    def _render_header(self, device: DeviceInfo):
        raise NotImplementedError

    def _visible_procs(self, device: DeviceInfo) -> List[ProcInfo]:
        raise NotImplementedError

    def _empty_message(self) -> str:
        raise NotImplementedError

    def _columns_for(self, procs: List[ProcInfo]) -> List[Column]:
        raise NotImplementedError


class GpuCard(DeviceCard):
    """One GPU: title, stats line, proportion bar, legend and process table."""

    def __init__(self, monitor: Monitor, zen: bool = False, danger: bool = False):
        super().__init__(monitor, danger=danger)
        self.bar = MemoryBar()
        self.legend = Static(classes="legend")
        self.zen = zen

    def compose(self):
        yield self.stats
        yield self.bar
        yield self.legend
        yield self.proc_table

    def set_zen(self, zen: bool):
        if zen == self.zen:
            return
        self.zen = zen
        if self._device is not None and self.is_mounted:
            self._apply(self._device)

    def _render_header(self, device: DeviceInfo):
        assert isinstance(device, GPUInfo)
        gpu = device
        self.border_title = f" # {gpu.id}  <{gpu.name}> "
        self._update_stats(gpu)
        self._update_bar(gpu)
        self._update_legend(gpu)

    def _visible_procs(self, device: DeviceInfo) -> List[ProcInfo]:
        """In zen mode the table is filtered down to the watched user."""
        if self.zen:
            return device.user_procs.get(self.monitor.focus_user, [])
        return device.procs

    # Normal mode shows every column; zen mode uses the metadata layout.
    def _columns_for(self, procs: List[ProcInfo]) -> List[Column]:
        if self.zen:
            return columns_with_meta(ZEN_COLUMNS, procs, mode="zen")
        return GLOBAL_COLUMNS or [COLS["PID"]]

    def _empty_message(self) -> str:
        if self.zen:
            return f"— no processes for [{self.monitor.focus_user}] —"
        return "— no compute processes —"

    def _update_stats(self, gpu: GPUInfo):
        rate = gpu.idle_rate
        if rate <= config.MEM_FREE_CRIT:
            free_style = f"bold {config.COLOR_CRIT}"
        elif rate <= config.MEM_FREE_WARN:
            free_style = f"bold {config.COLOR_WARN}"
        else:
            free_style = f"bold {config.COLOR_OK}"

        line = Text(no_wrap=True, overflow="ellipsis")
        line.append("[PROC] ", style="bold")
        line.append(f"{len(gpu.procs)}    ", style="bold")
        line.append("[USED] ", style="bold")
        line.append(f"{fmt_gb(gpu.mem_used)}", style="bold")
        line.append(f" / {fmt_gb(gpu.mem_total)} GB", style="dim")
        line.append(f" ({gpu.used_rate * 100:.0f}%)    ")
        line.append("[FREE] ", style="bold")
        line.append(f"{fmt_gb(gpu.mem_free)} GB ", style=free_style)
        if gpu.mem_util >= 0:
            line.append("   [⚡] ", style="bold")
            line.append(f"{gpu.mem_util}%", style=config.TEMP_COLOR)
        if gpu.temperature >= 0:
            line.append("   [\U0001f321] ", style="bold")
            temp_style = config.COLOR_CRIT if gpu.temperature >= config.TEMP_WARN_C else config.TEMP_COLOR
            line.append(f"{gpu.temperature}°C", style=temp_style)
        self.stats.update(line)

    def _update_bar(self, gpu: GPUInfo):
        ordered = sorted(gpu.user_mems.items(), key=lambda kv: kv[1], reverse=True)
        segments = [(self.monitor.color_for(u), float(m)) for u, m in ordered]
        self.bar.set_data(segments, float(gpu.mem_total))

    def _update_legend(self, gpu: GPUInfo):
        ordered = sorted(gpu.user_mems.items(), key=lambda kv: kv[1], reverse=True)
        legend = Text(no_wrap=True, overflow="ellipsis")
        if not ordered:
            legend.append("idle", style="dim")
            self.legend.update(legend)
            return
        mvp = ordered[0][0]
        for user, mem in ordered:
            color = self.monitor.color_for(user)
            pct = mem / gpu.mem_total * 100 if gpu.mem_total else 0
            legend.append("\U0001f3c6 " if user == mvp else "")
            if self.zen and user == self.monitor.focus_user:
                legend.append("★ ", style=color)
                legend.append(user, style=f"bold underline {color}")
            else:
                legend.append(f"● {user}", style=color)
            legend.append(f" {fmt_gb(mem)} GB ({pct:.0f}%)   ")
        self.legend.update(legend)


class CpuCard(DeviceCard):
    """The watched user's non-GPU, scopos-reporting processes (host-RAM view).

    Parallel to :class:`GpuCard` but with no GPU memory column and no bar/legend.
    """

    def compose(self):
        yield self.stats
        yield self.proc_table

    def _render_header(self, device: DeviceInfo):
        self.border_title = f" \U0001f9ee  <{device.name}>  ·  tracked process(es) of {self.monitor.focus_user} via scopos API "
        rss_total = sum(p.rss for p in device.procs)
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(f"[PROC] {len(device.procs)}", style="bold")
        line.append(f"    [RAM] {fmt_gb(rss_total)} GB", style="bold")
        self.stats.update(line)

    def _visible_procs(self, device: DeviceInfo) -> List[ProcInfo]:
        return device.user_procs.get(self.monitor.focus_user, [])

    def _columns_for(self, procs: List[ProcInfo]) -> List[Column]:
        return columns_with_meta(CPU_COLUMNS, procs, mode="cpu")

    def _empty_message(self) -> str:
        return "— no scopos-reporting processes —"
