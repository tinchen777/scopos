# -*- coding: utf-8 -*-
"""Reusable Textual widgets for Scopos."""

from __future__ import annotations
import time
from rich.text import Text
from textual.widgets import Static

from .. import __version__
from ..monitor import Monitor


LOGO = r"""  ___   ___  _____  ____  _____  ___  
 / __) / __)(  _  )(  _ \(  _  )/ __) 
 \__ \( (__  )(_)(  )___/ )(_)( \__ \ 
 (___/ \___)(_____)(__)  (_____)(___/ """


class Logo(Static):
    """The SCOPOS ASCII logo, pinned top-left."""

    def __init__(self):
        text = Text(LOGO, style="bold cyan")
        text.append(f"  v{__version__}", style="dim white")
        super().__init__(text)


class SysMeter(Static):
    """Compact host RAM / swap usage bars, shown next to the logo."""

    DEFAULT_CSS = """
    SysMeter {
        width: auto;
        height: auto;
    }
    """

    BAR_WIDTH = 26

    def __init__(self, monitor: Monitor):
        super().__init__()
        self.monitor = monitor

    def on_mount(self):
        self.refresh_stats()
        self.set_interval(2.0, self.refresh_stats)

    def refresh_stats(self):
        stats = self.monitor.system_stats()
        text = Text(justify="right")
        text.append(self._line("Mem", *stats["mem"]))
        text.append("\n")
        text.append(self._line("Swp", *stats["swap"]))
        self.update(text)

    def _line(self, label: str, used: float, total: float) -> Text:
        total = total or 1
        frac = max(0.0, min(1.0, used / total))
        if frac >= 0.85:
            color = "red"
        elif frac >= 0.6:
            color = "yellow"
        else:
            color = "green"
        filled = round(frac * self.BAR_WIDTH)
        gb = 1024 ** 3
        line = Text()
        line.append(f"{label} ", style="bold")
        line.append("▕", style="grey50")
        line.append("█" * filled, style=color)
        line.append("░" * (self.BAR_WIDTH - filled), style="grey35")
        line.append("▏", style="grey50")
        line.append(f" {used / gb:5.1f} / {total / gb:5.1f} GB", style="dim")
        line.append(f" {frac * 100:3.0f}%", style=color)
        return line


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
