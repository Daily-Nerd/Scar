"""3-way output dispatch for the human-facing read commands (Issue #78).

Each read command builds a structured data object, then routes it here:

1. ``--json``           → ``json.dumps(data, indent=2)`` — the stable machine contract.
2. ``sys.stdout.isatty()`` → the Rich renderer (Table/Panel/colour). Pretty path.
3. otherwise (piped / captured / non-tty) → the legacy plain ``print()`` output,
   byte-for-byte unchanged.

Critical invariant: Rich must NEVER run in the non-tty branch. Rich wraps and
truncates to ~80 cols, which would break the long-path substring assertions the
test suite (and CI consumers) rely on. The plain renderer is the source of truth
for non-tty; Rich is strictly additive on top of a real terminal.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from rich.console import Console

# One shared console for every Rich-rendered surface — keeps width/colour policy
# in a single place. ``file`` is resolved lazily by Rich at print time, so tests
# that swap sys.stdout (capsys) still capture output.
console = Console()


def is_tty() -> bool:
    """True when stdout is an interactive terminal. Pulled out as a function so
    tests can monkeypatch the branch without faking a real tty."""
    return sys.stdout.isatty()


def render(
    *,
    data: Any,
    json_flag: bool,
    tty: Callable[[], None],
    plain: Callable[[], None],
) -> None:
    """Dispatch one command's output the 3 ways. ``data`` is the structured
    object (only consumed by the JSON branch); ``tty`` and ``plain`` are
    zero-arg renderers that close over the shared console / legacy print lines."""
    if json_flag:
        print(json.dumps(data, indent=2))
        return
    if is_tty():
        tty()
        return
    plain()
