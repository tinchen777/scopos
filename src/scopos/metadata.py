# -*- coding: utf-8 -*-
"""Shared metadata storage for Scopos.

This module is the bridge between a running program (a Python training
script, say) and the Scopos TUI.  A program reports arbitrary fields about
itself through :mod:`scopos.api`; those fields are written here as a small
JSON file named after the program's PID, under ``~/.scopos/metadata``.  The
TUI's :class:`~scopos.monitor.Monitor` reads the same files back and attaches
the fields to the matching GPU process so they can be shown in "zen" mode.

It deliberately has **no** third-party dependencies (no Textual, no NVML) so
it can be imported by any Python program that just wants to talk to Scopos.
"""

from __future__ import annotations
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Marker key identifying a value as a progress bar rather than a plain field.
# The TUI looks for this key to decide whether to render an animated bar.
PROGRESS_MARKER = "__scopos_progress__"


def metadata_dir() -> Path:
    """Return the directory holding per-process metadata files.

    Honours ``$SCOPOS_HOME`` so tests (and unusual setups) can redirect it;
    defaults to ``~/.scopos``.
    """
    home = os.environ.get("SCOPOS_HOME")
    base = Path(home) if home else Path.home() / ".scopos"
    return base / "metadata"


def metadata_path(pid: int) -> Path:
    return metadata_dir() / f"{pid}.json"


def ensure_dir() -> Path:
    d = metadata_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


# -- progress fields -------------------------------------------------------
def make_progress(
    value: Optional[float] = None,
    total: Optional[float] = None,
    label: Optional[str] = None,
    color: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a special progress-bar value to store in a metadata field.

    * ``value`` with ``total``  -> a determinate bar at ``value / total``
      (e.g. ``make_progress(3, 100)``), with a default ``"3/100"`` label.
    * ``value`` alone           -> a determinate bar; ``value`` is treated as
      an already-computed fraction in ``[0, 1]``.
    * ``value=None``            -> an *indeterminate* bar, animated by the TUI.

    The returned ``dict`` is JSON-serialisable and is recognised by the TUI
    via :data:`PROGRESS_MARKER`.
    """
    frac: Optional[float]
    auto_label: Optional[str] = None
    if value is None:
        frac = None
    elif total is not None:
        frac = (value / total) if total else 0.0
        auto_label = f"{_trim(value)}/{_trim(total)}"
    else:
        frac = float(value)
    if frac is not None:
        frac = max(0.0, min(1.0, frac))
    return {
        PROGRESS_MARKER: True,
        "value": frac,
        "label": label if label is not None else auto_label,
        "color": color,
    }


def is_progress(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get(PROGRESS_MARKER))


def _trim(num: float):
    """Render a number without a trailing ``.0`` so ``3.0`` shows as ``3``."""
    if isinstance(num, float) and num.is_integer():
        return int(num)
    return num


# -- reading ---------------------------------------------------------------
def read_fields(pid: int) -> Dict[str, Any]:
    """Return the fields reported by ``pid``; ``{}`` if none / unreadable."""
    try:
        with open(metadata_path(pid), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict):
        fields = data.get("fields")
        if isinstance(fields, dict):
            return fields
    return {}


# -- writing ---------------------------------------------------------------
def write_fields(pid: int, fields: Dict[str, Any], merge: bool = True) -> None:
    """Persist ``fields`` for ``pid`` atomically.

    With ``merge=True`` (the default) the new fields are layered on top of
    whatever was reported before, so callers can update one field at a time.
    """
    ensure_dir()
    if merge:
        merged = read_fields(pid)
        merged.update(fields)
    else:
        merged = dict(fields)
    payload = {"pid": pid, "updated_at": time.time(), "fields": merged}
    _atomic_write(metadata_path(pid), payload)


def clear(pid: int) -> None:
    """Remove the metadata file for ``pid`` (no error if it is missing)."""
    try:
        os.remove(metadata_path(pid))
    except OSError:
        pass


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
