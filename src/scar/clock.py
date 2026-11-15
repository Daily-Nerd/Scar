"""The one reader of the wall clock (#380).

"What day is it" has exactly one owner so every surface agrees and a test can
fix the date in a single place. The frame is local time of the machine running
the command: dates written into scars stay in the author's calendar and the
firing log is naive local time (SPEC section on stats windows).

Callers use `clock.today()` / `clock.now()` through the module (not
`from .clock import today`) so a test can monkeypatch the owner once.
Stdlib-only and cheap to import: hooks.py loads this on the hot path.
"""
from __future__ import annotations

import time


def today() -> str:
    """Local date as YYYY-MM-DD."""
    return time.strftime("%Y-%m-%d")


def now() -> str:
    """Firing-log timestamp: local naive YYYY-MM-DDTHH:MM:SS."""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def month_index() -> int:
    """Current year*12+month in local time, derived from today()."""
    year, month, _day = today().split("-")
    return int(year) * 12 + int(month)
