# -*- coding: utf-8 -*-
"""Reusable Textual widgets for Scopos."""

from __future__ import annotations
import time
from rich.text import Text
from textual.widgets import Static
from typing import Optional

from .. import (__version__, __author__)
from .. import config
from ..monitor import Monitor

LOGO_UP = r"""  ___   ___  _____  ____  _____  ___  
 / __) / __)(  _  )(  _ \(  _  )/ __) """
LOGO_DOWN = r""" \__ \( (__  )(_)(  )___/ )(_)( \__ \ 
 (___/ \___)(_____)(__)  (_____)(___/ """


class Logo(Static):
    """The SCOPOS ASCII logo, pinned top-left."""

    def __init__(self):
        text = Text(LOGO_UP, style="bold cyan")
        text.append(f"  v{__version__}\n", style="dim white")
        text.append(LOGO_DOWN, style="bold cyan")
        text.append(f"by {__author__}", style="white italic")

        super().__init__(text)


class SysMeter(Static):
    """Compact host RAM / swap usage bars, shown next to the logo."""

    DEFAULT_CSS = """
    SysMeter {
        width: auto;
        height: auto;
    }
    """

    def __init__(self, monitor: Monitor):
        super().__init__()
        self.monitor = monitor

    def on_mount(self):
        self.refresh_stats()
        # Re-render once layout has settled (sibling sizes are known by then).
        self.call_after_refresh(self.refresh_stats)
        self.set_interval(2.0, self.refresh_stats)

    def refresh_stats(self):
        stats = self.monitor.system_stats()
        budget = self._budget()
        text = Text(justify="right")
        text.append(self._line("Mem", *stats["mem"], budget=budget))
        text.append("\n")
        text.append(self._line("Swp", *stats["swap"], budget=budget))
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
            for widget in (Logo, Clock):
                try:
                    used += self.app.query_one(widget).region.width
                except Exception:
                    pass
            # Leave a little room for the top-bar padding / breathing space.
            avail = total - used - 6 if used else full
        except Exception:
            avail = full
        return max(config.SYS_METER_MIN, min(full, avail))

    def _full_width(self) -> int:
        # Widest meter: "Mem " + ▕bar▏ + " 1234.5 / 1234.5 GB" + " 100%".
        return 4 + 1 + config.SYS_BAR_MAX + 1 + 19 + 5

    def _line(self, label: str, used: float, total: float, budget: int) -> Text:
        total = total or 1
        frac = max(0.0, min(1.0, used / total))
        if frac >= config.SYS_MEM_CRIT:
            color = config.COLOR_CRIT
        elif frac >= config.SYS_MEM_WARN:
            color = config.COLOR_WARN
        else:
            color = config.COLOR_OK
        gb = 1024 ** 3
        prefix = f"{label} "
        # Fixed-width so the two lines (Mem / Swp) always line up.
        gb_txt = f" {used / gb:5.1f} / {total / gb:5.1f} GB"
        pct_txt = f" {frac * 100:3.0f}%"
        ends = 2  # the ▕ ▏ bar caps

        # Largest-first: try bar + both texts, then drop GB, then drop the
        # percentage, shrinking the bar toward BAR_MIN as space runs out.
        show_gb = show_pct = True
        for show_gb, show_pct in ((True, True), (False, True), (False, False)):
            suffix = (len(gb_txt) if show_gb else 0) + (len(pct_txt) if show_pct else 0)
            bar = budget - len(prefix) - ends - suffix
            if bar >= config.SYS_BAR_MIN:
                break
        bar = max(1, min(config.SYS_BAR_MAX, bar))
        filled = round(frac * bar)

        line = Text()
        line.append(prefix, style="bold")
        line.append("▕", style="grey50")
        line.append("█" * filled, style=color)
        line.append("░" * (bar - filled), style=config.BAR_TRACK_COLOR)
        line.append("▏", style="grey50")
        if show_gb:
            line.append(gb_txt, style="dim")
        if show_pct:
            line.append(pct_txt, style=color)
        return line


class Clock(Static):
    """Timestamp of the latest data refresh, pinned top-right.

    It does not tick on its own; the app calls :meth:`show_time` after every
    data refresh (auto interval or a manual ``r``), so the time on screen always
    reflects when the displayed data was collected.
    """

    def __init__(self, interval: int):
        super().__init__()
        self.interval = interval  # kept for compatibility; no longer self-ticks

    def on_mount(self):
        # Initial value; replaced by the app's first refresh moments later.
        self.show_time()

    def show_time(self, ts: Optional[float] = None):
        now = time.localtime(ts) if ts is not None else time.localtime()
        text = Text(justify="left")
        text.append(time.strftime("%Y-%m-%d\n", now), style="bold")
        text.append(time.strftime("  %A\n", now), style="italic")
        text.append(time.strftime(" %H:%M:%S", now), style="bold cyan")
        self.update(text)

    # Backwards-compatible alias.
    update_clock = show_time
