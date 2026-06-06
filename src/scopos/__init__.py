# src/scopos/__init__.py
"""
SCOPOS
======

a Textual TUI for monitoring GPU memory usage per user.

It also exposes a small, dependency-light API so running programs can push
live status (plain fields and progress bars) to the monitor::

    import scopos
    scopos.report(stage="train", progress=scopos.progress(3, 100))
"""

__author__ = "Zhen Tian"
__version__ = "2.1.0"

# Public reporting API. Kept import-light (no Textual / NVML) so any program
# can ``import scopos`` just to report status.
from .api import (  # noqa: E402,F401
    clear,
    metadata_file,
    progress,
    report,
    session,
    set,
    update,
)

__all__ = [
    "report",
    "update",
    "set",
    "progress",
    "clear",
    "session",
    "metadata_file",
]
