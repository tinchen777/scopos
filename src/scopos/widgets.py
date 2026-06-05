# -*- coding: utf-8 -*-
"""Reusable Textual widgets for Scopos."""

from __future__ import annotations
from rich.text import Text
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import (DataTable, Static)
from typing import (List, Tuple)

from .monitor import (GPUInfo, Monitor, fmt_gb)


LOGO = r"""  ___   ___  _____  ____  _____  ___
 / __) / __)(  _  )(  _ \(  _  )/ __)
 \__ \( (__  )(_)(  )___/ )(_)( \__ \
 (___/ \___)(_____)(__)  (_____)(___/"""


class Logo(Static):
    """The SCOPOS ASCII logo, pinned top-left."""

    def __init__(self):
        text = Text(LOGO, style="bold cyan")
        super().__init__(text)


class MemoryBar(Widget):
    """A single-line bar whose coloured segments show each user's share.

    The bar always fills its own width, so it grows and shrinks with the
    terminal - that is what gives the "直观占比" (intuitive proportion) view.
    """

    DEFAULT_CSS = """
    MemoryBar {
        height: 1;
        width: 1fr;
    }
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
            text.append("░" * (width - used), style="grey35")
        return text


class GpuCard(Vertical):
    """One GPU: header, stats line, proportion bar, legend and process table."""

    DEFAULT_CSS = """
    GpuCard {
        height: auto;
        border: round $primary;
        border-title-color: $text;
        border-title-style: bold;
        padding: 0 1;
        margin: 0;
    }
    GpuCard .stats { height: 1; }
    GpuCard .legend { height: auto; color: $text-muted; }
    GpuCard DataTable {
        height: auto;
        max-height: 20;
        margin-top: 1;
    }
    """

    def __init__(self, monitor: Monitor, show_detail: bool):
        super().__init__()
        self.monitor = monitor
        self.show_detail = show_detail
        self.stats = Static(classes="stats")
        self.bar = MemoryBar()
        self.legend = Static(classes="legend")
        self.table = DataTable(zebra_stripes=True, cursor_type="row")
        self._pending: GPUInfo | None = None

    def compose(self):
        yield self.stats
        yield self.bar
        yield self.legend
        yield self.table

    def on_mount(self):
        cols = ["", "PID", "PROC", "USER", "NO.", "MEM/GB", "STARTED", "RUNTIME"]
        if self.show_detail:
            cols.append("DETAIL")
        self.table.add_columns(*cols)
        if self._pending is not None:
            self._apply(self._pending)

    # -- updating ----------------------------------------------------------
    def update(self, gpu: GPUInfo):
        # A card may be updated in the same frame it is mounted, before its
        # columns exist; defer until on_mount in that case.
        if not self.is_mounted:
            self._pending = gpu
            return
        self._apply(gpu)

    def _apply(self, gpu: GPUInfo):
        self._pending = None
        self.border_title = f" #{gpu.index}  {gpu.name} "
        self._update_stats(gpu)
        self._update_bar(gpu)
        self._update_legend(gpu)
        self._update_table(gpu)

    def _update_stats(self, gpu: GPUInfo):
        rate = gpu.idle_rate
        if rate <= 0.15:
            remain_style = "bold red"
        elif rate <= 0.5:
            remain_style = "bold yellow"
        else:
            remain_style = "bold green"

        line = Text(no_wrap=True, overflow="ellipsis")
        line.append("USED ", style="bold")
        line.append(f"{fmt_gb(gpu.mem_used)}", style="bold")
        line.append(f" / {fmt_gb(gpu.mem_total)} GB", style="dim")
        line.append(f"  ({gpu.used_rate * 100:.0f}%)   ")
        line.append("FREE ", style=remain_style)
        line.append(f"{fmt_gb(gpu.mem_free)} GB", style=remain_style)
        if gpu.util >= 0:
            line.append(f"   ⚡{gpu.util}%", style="cyan")
        if gpu.temperature >= 0:
            temp_style = "red" if gpu.temperature >= 80 else "cyan"
            line.append(f"   🌡{gpu.temperature}°C", style=temp_style)
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
            legend.append("● ", style=color)
            crown = " 🏆" if user == mvp else ""
            legend.append(f"{user} {fmt_gb(mem)}G {pct:.0f}%{crown}   ")
        self.legend.update(legend)

    def _update_table(self, gpu: GPUInfo):
        self.table.clear()
        for proc in gpu.procs:
            color = self.monitor.color_for(proc.user)
            row = [
                Text("●", style=color),
                str(proc.pid),
                proc.name,
                Text(proc.user, style=color),
                proc.number,
                fmt_gb(proc.mem),
                proc.started,
                proc.runtime,
            ]
            if self.show_detail:
                row.append(proc.detail)
            self.table.add_row(*row)
        if not gpu.procs:
            empty = ["" for _ in range(8 + (1 if self.show_detail else 0))]
            empty[2] = "— no compute processes —"
            self.table.add_row(*[Text(c, style="dim") for c in empty])
