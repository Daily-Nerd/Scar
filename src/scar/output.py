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


# --- Formatted printing (#414) -------------------------------------------------
#
# Rich reads square brackets in any str it renders as style tags, so a value
# such as "[#5]", "app/[id]/page.tsx" or "x[/]y" is eaten, mangled or raises
# MarkupError. Markup and data are kept apart here, in one place:
#
# * a bare ``str`` handed to any helper below is DATA and prints literally;
# * markup is only ever built with ``fmt()``, whose first argument is a literal
#   written at the call site and whose values are data;
# * ``Markup`` marks a fragment that is already safe to hand to rich.
#
# A value is never escaped with backslashes: that scheme breaks at the edges
# (a trailing backslash or bracket combines with the literal that follows it).
# Instead its "[" and "\" are swapped for private-use stand-ins, so the markup
# parser sees no bracket that came from data, and the stand-ins are swapped
# back after parsing. The swap is one character for one character, so the
# style spans stay where they were.
#
# tests/test_rich_markup_values.py fails if a call site builds a dynamic string
# for ``console.print`` or for these helpers instead of going through ``fmt``.

_LBRACKET = "\ue000"
_BACKSLASH = "\ue001"
_STAND_INS = str.maketrans({"[": _LBRACKET, "\\": _BACKSLASH,
                            _LBRACKET: "\ufffd", _BACKSLASH: "\ufffd"})
_RESTORE = str.maketrans({_LBRACKET: "[", _BACKSLASH: "\\"})


class Markup(str):
    """Rich markup that is safe to render: built by ``fmt``, ``plain`` or
    ``ref`` from a literal plus data, never from raw data."""

    __slots__ = ()


def _lit(value: Any) -> Any:
    """A value as it may sit inside markup. Numbers are left alone so format
    specs such as ``{:>3}`` work; Markup passes through; the rest is data."""
    if isinstance(value, Markup):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return str(value).translate(_STAND_INS)


def fmt(markup: str, *values: Any, **named: Any) -> Markup:
    """Fill ``{}`` fields in a literal markup string. The literal is trusted
    (it is written in the source); every value is data."""
    return Markup(markup.format(*(_lit(v) for v in values),
                                **{k: _lit(v) for k, v in named.items()}))


def plain(value: Any) -> Markup:
    """One value, rendered literally."""
    return Markup(_lit(value))


def styled(style: str, value: Any) -> Markup:
    """One value, rendered literally, in a rich style. ``style`` is a name
    written at the call site ("red", "bold"), never data."""
    return Markup(f"[{style}]{_lit(value)}[/]")


def bracketed(value: Any) -> Markup:
    """One value inside literal square brackets, e.g. a status like ``[active]``."""
    return plain(f"[{value}]")


def ref(scar_id: Any) -> Markup:
    """The ``[#N]`` label a scar carries in a line of output."""
    return plain(f"[#{scar_id}]")


def block(lines: Any, sep: str = "\n") -> Markup:
    """Join fragments into one body. Each item is a Markup or literal data."""
    return Markup(sep.join(_lit(x) for x in lines))


def _render(value: Any, highlight: bool = False) -> Any:
    """What rich is given for an argument: a str or Markup becomes a Text (a
    bare str is data, a Markup is parsed), anything else is already a rich
    renderable and passes through. ``say`` highlights, as ``console.print`` did
    for a markup string; panels and tables never did."""
    if value is None or not isinstance(value, str):
        return value
    from rich.text import Text

    text = Text.from_markup(value) if isinstance(value, Markup) else Text(value)
    text.plain = text.plain.translate(_RESTORE)
    return console.highlighter(text) if highlight else text


def say(value: Any = "", **kwargs: Any) -> None:
    """Print one line (or renderable) on the shared console."""
    console.print(_render(value, highlight=True), **kwargs)


def show(renderable: Any, **kwargs: Any) -> None:
    """Print a rich object built by ``table`` / ``panel``. A bare string is
    refused: use ``say`` for text."""
    if isinstance(renderable, str):
        raise TypeError("show() takes a rich renderable; use say() for text")
    console.print(renderable, **kwargs)


def panel(body: Any, *, title: Any = None, subtitle: Any = None, fit: bool = False,
          **kwargs: Any) -> Any:
    from rich.panel import Panel

    build = Panel.fit if fit else Panel
    return build(_render(body), title=_render(title), subtitle=_render(subtitle), **kwargs)


def table(title: Any, columns: Any, rows: Any, **kwargs: Any) -> Any:
    """``columns`` are names or ``(name, options)`` pairs; every cell is data
    unless it is a Markup."""
    from rich.table import Table

    heading = _render(title)
    if hasattr(heading, "stylize"):
        # rich styles a str title itself; a Text title has to be styled here.
        heading.stylize("table.title")
    t = Table(title=heading, **kwargs)
    for col in columns:
        name, opts = (col, {}) if isinstance(col, str) else col
        t.add_column(_render(name), **opts)
    for row in rows:
        t.add_row(*(_render(cell) for cell in row))
    return t
