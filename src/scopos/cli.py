# -*- coding: utf-8 -*-
"""Command line entry point: ``python -m scopos``."""

from __future__ import annotations
import argparse

from . import __version__
from .app import ScoposApp


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scopos",
        description="Monitor GPU memory usage, grouped by user (Textual TUI).",
    )
    parser.add_argument(
        "-t",
        "--theme",
        default="ansi-dark",
        help="Textual theme to use (default: ansi-dark). See https://textual.textualize.io/themes/ for options.",
    )
    parser.add_argument(
        "-u",
        "--user",
        default="",
        help="Highlight this user and show their shell-script task details.",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=int,
        default=5,
        help="Refresh interval in seconds (default: 5).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with synthetic GPU data (no NVIDIA driver required).",
    )
    parser.add_argument(
        "-z",
        "--zen",
        action="store_true",
        help="Start in zen (focus) mode: tables show only --user's processes "
        "plus the fields they report via the scopos Python API. Toggle with 'z'.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"scopos {__version__}",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    app = ScoposApp(
        watch_user=args.user,
        interval=args.interval,
        demo=args.demo,
        theme=args.theme,
        zen=args.zen,
    )
    app.run()
