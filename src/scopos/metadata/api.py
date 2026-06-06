# -*- coding: utf-8 -*-
"""Public Python API for talking to the Scopos TUI.

Import this from any program whose GPU usage you watch with ``scopos`` and
push live status to the monitor::

    import scopos

    scopos.report(stage="train", loss=0.123)
    scopos.report(epoch=scopos.progress(3, 100))   # an animated bar

    # ... or replace everything at once and clean up afterwards:
    with scopos.session(stage="warmup"):
        for step in range(steps):
            scopos.report(progress=scopos.progress(step, steps))

Each call writes/updates ``~/.scopos/metadata/<pid>.json``.  When you run
``scopos`` (especially in zen mode, ``z``), those fields appear as extra
columns next to your process, and :func:`progress` values render as live
progress bars.

The API has no heavy dependencies, so importing :mod:`scopos` for reporting
does not require Textual or an NVIDIA driver to be installed.
"""

from __future__ import annotations
import atexit
import os
from typing import Any

from . import utils

__all__ = ["report", "update", "set", "progress", "clear", "session", "metadata_file"]

_cleanup_registered = False


def _register_cleanup():
    global _cleanup_registered
    if not _cleanup_registered:
        atexit.register(clear)
        _cleanup_registered = True


def report(**fields: Any):
    """Report (merge) one or more fields for the current process.

    Existing fields are kept; fields passed here are added or overwritten.
    Pass ``None`` as a value to drop a previously reported field.
    """
    pid = os.getpid()
    drop = {k for k, v in fields.items() if v is None}
    add = {k: v for k, v in fields.items() if v is not None}
    if drop:
        # Read-modify-write so explicit Nones remove fields rather than store them.
        merged = utils.read_fields(pid)
        merged.update(add)
        for k in drop:
            merged.pop(k, None)
        utils.write_fields(pid, merged, merge=False)
    else:
        utils.write_fields(pid, add, merge=True)
    _register_cleanup()


# ``update`` is an alias for ``report`` for callers who prefer the name.
update = report


def set(**fields: Any):  # noqa: A001 - intentional public name
    """Replace *all* reported fields with ``fields`` for the current process."""
    utils.write_fields(os.getpid(), dict(fields), merge=False)
    _register_cleanup()


def progress(value=None, total=None, label=None, color=None):
    """Create a progress-bar value for use in :func:`report`/:func:`set`.

    See :func:`scopos.metadata.make_progress` for the accepted forms.
    """
    return utils.make_progress(value=value, total=total, label=label, color=color)


def clear():
    """Remove this process's metadata file (called automatically at exit)."""
    utils.clear(os.getpid())


def metadata_file() -> str:
    """Return the path of this process's metadata file (may not exist yet)."""
    return str(utils.metadata_path(os.getpid()))


class session:
    """Context manager that reports fields on entry and clears them on exit.
    >>> with scopos.session(stage="train"):
    >>>     train()
    """

    def __init__(self, **fields: Any):
        self._fields = fields

    def __enter__(self) -> "session":
        if self._fields:
            report(**self._fields)
        else:
            _register_cleanup()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        clear()
        return False
