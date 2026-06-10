# -*- coding: utf-8 -*-
"""Reusable Textual widgets for Scopos."""

from __future__ import annotations
import time
import psutil
from collections import deque
from rich.text import Text
from textual.widgets import Static
from typing import Optional

from .. import (__version__, __author__)
from .. import config
from ._utils import fmt_gb

LOGO_UP = r"""  ___   ___  _____  ____  _____  ___  
 / __) / __)(  _  )(  _ \(  _  )/ __) """
LOGO_DOWN = r""" \__ \( (__  )(_)(  )___/ )(_)( \__ \ 
 (___/ \___)(_____)(__)  (_____)(___/ """


class Logo(Static):
    """The SCOPOS ASCII logo."""

    def __init__(self, id: str):
        text = Text()
        text.append(LOGO_UP, style="bold cyan")
        text.append(f"  v{__version__}\n", style="dim white")
        text.append(LOGO_DOWN, style="bold cyan")
        text.append(f"by {__author__}", style="white italic")

        super().__init__(text, id=id)


class Clock(Static):
    """Timestamp of the latest data refresh, pinned top-right.

    It does not tick on its own; the app calls :meth:`show_time` after every
    data refresh (auto interval or a manual ``r``), so the time on screen always
    reflects when the displayed data was collected.
    """

    def __init__(self, id: str):
        super().__init__(id=id)

    def on_mount(self):
        # Initial value; replaced by the app's first refresh moments later.
        self.show_time()

    def show_time(self, ts: Optional[float] = None):
        now = time.localtime(ts) if ts is not None else time.localtime()
        text = Text(justify="full")
        text.append(time.strftime("%Y-%m-%d\n", now), style="bold")
        text.append(time.strftime("%A\n", now), style="italic")
        text.append(time.strftime("%H:%M:%S", now), style="bold cyan")
        self.update(text)

    # Backwards-compatible alias.
    update_clock = show_time


USAGE_CHARS = [" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "█"]

# USAGE_CHARS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]

class CPUMeter(Static):
    """Display CPU usage for multiple cores in a 2x4 grid, rough visualization."""

    DEFAULT_CSS = """
    CPUMeter {
        width: auto;
        height: auto;
    }
    """

    def __init__(self, interval: int, id: str):
        super().__init__(id=id)

        self.interval = interval
        self.history = deque(
            [(USAGE_CHARS[0], USAGE_CHARS[0])]*config.CPU_USAGE_LEN,
            maxlen=config.CPU_USAGE_LEN
        )

    def on_mount(self):
        self.refresh_stats()
        self.set_interval(self.interval, self.refresh_stats)

    def refresh_stats(self):
        cpu_usage = psutil.cpu_percent()
        self.history.append(self._render_cpu_chars(cpu_usage))
        self.update(self._assemble_text(cpu_usage))

    @staticmethod
    def _render_cpu_chars(cpu_usage: float):
        state_idx = int(len(USAGE_CHARS) * 2 * cpu_usage / 100) - 1
        if state_idx >= len(USAGE_CHARS):
            upper = USAGE_CHARS[state_idx - len(USAGE_CHARS)]
            lower = USAGE_CHARS[-1]
        else:
            upper = USAGE_CHARS[0]
            lower = USAGE_CHARS[state_idx]

        return upper, lower

    def _assemble_text(self, cpu_usage: float):
        if cpu_usage >= config.CPU_USAGE_CRIT:
            color = config.COLOR_CRIT
        elif cpu_usage >= config.CPU_USAGE_WARN:
            color = config.COLOR_WARN
        else:
            color = config.COLOR_OK

        uppers, lowers = zip(*self.history)
        text = Text(justify="left")
        # header
        text.append(" " * config.CPU_USAGE_LEN, style="underline grey50")
        text.append("       \n", style="bold")
        # upper
        text.append("▕", style="grey50")
        text.append("".join(uppers), style="green")
        text.append("▏", style="grey50")
        text.append("  CPU \n", style="bold")
        # lower
        text.append("▕", style="grey50")
        text.append("".join(lowers), style="green")
        text.append("▏", style="grey50")
        text.append(f"{cpu_usage:5.1f}%\n", style=color)
        # bottom
        text.append("‾" * config.CPU_USAGE_LEN, style="grey50")
        text.append("       ", style="bold")

        return text


class SysMeter(Static):
    """Compact host RAM / swap usage bars, shown next to the logo."""

    DEFAULT_CSS = """
    SysMeter {
        width: auto;
        height: auto;
    }
    """

    def __init__(self, interval: int, id: str):
        super().__init__(id=id)

        self.interval = interval

    def on_mount(self):
        self.refresh_stats()
        # Re-render once layout has settled (sibling sizes are known by then).
        self.call_after_refresh(self.refresh_stats)
        self.set_interval(self.interval, self.refresh_stats)

    def refresh_stats(self):
        budget = self._budget()
        text = Text(justify="left")
        text.append(self._line("Mem", psutil.virtual_memory(), budget=budget))
        text.append("\n")
        text.append(self._line("Swp", psutil.swap_memory(), budget=budget))
        self.update(text)

    def _budget(self) -> int:
        """Cells available to a meter line, shrinking with the terminal.

        The top bar holds the logo and clock at their natural widths and lets
        flexible spacers absorb the rest, so the meter must fit whatever is
        left — otherwise it gets clipped and can't be read (the bug this fixes).
        """
        full = self._full_width()
        try:
            total = self.app.size.width
            used = 0
            for widget in (Logo, Clock, CPUMeter):
                try:
                    used += self.app.query_one(widget).region.width
                except Exception:
                    pass
            # Leave a little room for the top-bar padding / breathing space.
            avail = (total - used - 6) if used else full
        except Exception:
            avail = full
        return max(config.SYS_METER_MIN, min(full, avail))

    @staticmethod
    def _full_width() -> int:
        # Widest meter: "Mem" + ▕bar▏ + "234.5 / 234.5 GB" + " 100%".
        return 3 + 1 + config.SYS_BAR_MAX + 1 + 16 + 5

    @staticmethod
    def _line(label: str, stat, budget: int) -> Text:
        used = float(stat.used)
        total = float(stat.total) or 1
        frac = max(0.0, min(1.0, used / total))
        if frac >= config.SYS_MEM_CRIT:
            color = config.COLOR_CRIT
        elif frac >= config.SYS_MEM_WARN:
            color = config.COLOR_WARN
        else:
            color = config.COLOR_OK
        # Fixed-width so the two lines (Mem / Swp) always line up.
        gb_txt = f"{fmt_gb(used, 5, 1)} / {fmt_gb(total, 5, 1)} GB"
        pct_txt = f" {frac * 100:3.0f}%"

        # Largest-first: try bar + both texts, then drop GB, then drop the
        # percentage, shrinking the bar toward BAR_MIN as space runs out.
        show_gb = show_pct = True
        for show_gb, show_pct in ((True, True), (False, True), (False, False)):
            suffix = 2 + (len(gb_txt) if show_gb else 0) + (len(pct_txt) if show_pct else 0)
            bar = budget - len(label) - suffix
            if bar >= config.SYS_BAR_MIN:
                break
        bar = max(1, min(config.SYS_BAR_MAX, bar))
        filled = round(frac * bar)

        line = Text()
        line.append(label, style="bold")
        line.append("▕", style="grey50")
        line.append("|" * filled, style=color)
        line.append("|" * (bar - filled), style=config.BAR_TRACK_COLOR)
        line.append("▏", style="grey50")
        if show_gb:
            line.append(gb_txt, style="dim")
        if show_pct:
            line.append(pct_txt, style=color)
        return line
