"""Values printed in a terminal must come out literally (#414).

Rich reads square brackets in a string as style tags. These tests drive real
commands through the terminal branch and check the value survives.
"""
import ast
import io
import re
import subprocess
from pathlib import Path

import pytest

from scar.cli import main
from scar.store import init_scars


def _scar(*, id, title="plain title", path="src/gone/"):
    return (
        "---\n"
        f"id: {id}\n"
        "type: deadend\n"
        f"title: {title}\n"
        "severity: medium\n"
        "confidence: 0.8\n"
        "created: 2026-06-10\n"
        'authors: ["claude-code"]\n'
        "anchors:\n"
        f"  - path: {path}\n"
        "evidence:\n"
        "  - commit: abc1234\n"
        "status: active\n"
        "---\n\n"
        "Body text.\n"
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    return tmp_path


def _tty(monkeypatch):
    import scar.output as out
    monkeypatch.setattr(out, "is_tty", lambda: True)


def test_orphan_line_keeps_the_scar_id_and_a_bracketed_path(repo, capsys, monkeypatch):
    (repo / ".scars" / "0005-a.deadend.md").write_text(
        _scar(id=5, path="app/[id]/page.tsx"))
    _tty(monkeypatch)
    assert main(["orphan"]) == 0
    out = capsys.readouterr().out
    assert "[#5]" in out
    assert "app/[id]/page.tsx" in out


def test_status_table_survives_a_title_with_a_stray_closing_tag(repo, capsys, monkeypatch):
    (repo / ".scars" / "0005-a.deadend.md").write_text(
        _scar(id=5, title="x[/]y see [bold] here"))
    _tty(monkeypatch)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "x[/]y see [bold] here" in out
    assert "[#5]" in out


def test_why_panel_title_keeps_a_bracketed_title(repo, capsys, monkeypatch):
    (repo / "src").mkdir()
    (repo / ".scars" / "0005-a.deadend.md").write_text(
        _scar(id=5, title="x[/]y see [bold] here", path="src/"))
    _tty(monkeypatch)
    assert main(["why", "src/a.py"]) == 0
    out = capsys.readouterr().out
    assert "x[/]y see [bold] here" in out


def test_lint_orphan_warning_keeps_a_bracketed_path(repo, capsys, monkeypatch):
    (repo / ".scars" / "0005-a.deadend.md").write_text(
        _scar(id=5, path="app/[id]/page.tsx"))
    _tty(monkeypatch)
    assert main(["lint"]) == 0
    out = capsys.readouterr().out
    assert "app/[id]/page.tsx" in out


# --- the helper itself --------------------------------------------------------

VALUES = ["[#5]", "app/[id]/page.tsx", "see [bold] here", "x[/]y", "arr[0]", "a\\[b]",
          "ends with a backslash \\", "[red]"]


@pytest.fixture
def term(monkeypatch):
    from rich.console import Console
    import scar.output as out
    buf = io.StringIO()
    monkeypatch.setattr(out, "console", Console(
        file=buf, force_terminal=True, color_system="standard", width=200))
    return buf


def _strip(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.mark.parametrize("value", VALUES)
def test_fmt_renders_every_value_literally_and_keeps_the_label_styled(term, value):
    import scar.output as out
    out.say(out.fmt("[red]label[/] {} end", value))
    raw = term.getvalue()
    assert f"label {value} end" in _strip(raw)
    assert "\x1b[31m" in raw  # the red label still renders as a style


@pytest.mark.parametrize("value", VALUES)
def test_fmt_literal_value_inside_a_styled_span(term, value):
    import scar.output as out
    out.say(out.fmt("[dim]{}[/] tail", value))
    assert f"{value} tail" in _strip(term.getvalue())


@pytest.mark.parametrize("value", VALUES)
def test_fmt_named_values_and_plain(term, value):
    import scar.output as out
    out.say(out.fmt("[bold]{name}[/]: {other}", name=value, other=out.plain(value)))
    assert f"{value}: {value}" in _strip(term.getvalue())


def test_ref_renders_the_scar_label(term):
    import scar.output as out
    out.say(out.fmt("[red]orphan-detected[/] {} gone", out.ref(5)))
    assert "orphan-detected [#5] gone" in _strip(term.getvalue())


def test_numbers_pass_through_with_format_specs(term):
    import scar.output as out
    out.say(out.fmt("{:>3} of {}", 7, 9))
    assert "  7 of 9" in _strip(term.getvalue())


def test_nested_fmt_markup_is_not_escaped_twice(term):
    import scar.output as out
    out.say(out.fmt("{} #{}", out.fmt("[red]deadend[/]"), 5))
    raw = term.getvalue()
    assert "deadend #5" in _strip(raw)
    assert "\x1b[" in raw


@pytest.mark.parametrize("value", VALUES)
def test_panel_and_table_cells_take_values_literally(term, value):
    import scar.output as out
    t = out.table("T", ["a", "b"], [[out.plain(value), out.fmt("[red]{}[/]", value)]])
    out.show(t)
    out.show(out.panel(out.fmt("{}", value), title=out.fmt("t {}", value),
                       subtitle=out.fmt("[dim]{}[/]", value)))
    text = _strip(term.getvalue())
    assert text.count(value) >= 5


def test_show_refuses_a_bare_string(term):
    import scar.output as out
    with pytest.raises(TypeError):
        out.show("[red]oops[/]")


# --- structural guard ------------------------------------------------------------
#
# The class of bug is "markup and data built into one string". The only safe
# way to build a formatted rich line is output.fmt(<literal>, values...), so
# nothing may print through the console, or hand rich a string, any other way.

SRC = Path(__file__).resolve().parent.parent / "src" / "scar"
_SINKS = {"say", "fmt", "styled", "plain", "ref", "block"}
_RICH_OBJECTS = {"Panel", "Table", "Text", "add_row", "add_column"}


def _dynamic_string(node):
    """An f-string, %-format, .format() call or string concatenation."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                   or isinstance(n, ast.JoinedStr) for n in ast.walk(node))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"):
        return True
    return False


def _name(func):
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _offences(source: str, filename: str, *, is_output=False):
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func)
        recv = node.func.value if isinstance(node.func, ast.Attribute) else None
        recv_name = _name(recv) if recv is not None else ""
        # 1. no direct console.print outside output.py
        if name == "print" and recv_name == "console" and not is_output:
            found.append((node.lineno, "console.print outside output.py"))
            continue
        # 2. no dynamic string handed to a helper or to a rich object
        if name in _SINKS | _RICH_OBJECTS | {"panel", "table"} and not is_output:
            args = list(node.args) + [k.value for k in node.keywords]
            if any(_dynamic_string(a) for a in args):
                found.append((node.lineno, f"dynamic string passed to {name}()"))
    return found


def test_no_call_site_builds_markup_and_data_into_one_string():
    offences = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "output.py":
            continue
        for lineno, why in _offences(path.read_text(), path.name):
            offences.append(f"{path.name}:{lineno}: {why}")
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize("line", [
    'console.print(f"[red]x[/] {y}")',
    'output.console.print("[red]x[/] " + y)',
    'output.say(f"[red]x[/] {y}")',
    'output.say("[red]x[/] {}".format(y))',
    'output.say("[red]x[/] %s" % y)',
    'output.fmt(f"[red]{y}[/]")',
    'Panel(f"{y}", title="t")',
    't.add_row(f"#{y}", "b")',
    'output.panel("body", title=f"t {y}")',
])
def test_the_guard_catches_an_offending_line(line):
    assert _offences(line, "x.py")


@pytest.mark.parametrize("line", [
    'output.say(output.fmt("[red]x[/] {}", y))',
    'output.say("plain text")',
    'output.show(output.table("T", ["a"], rows))',
    'print(f"plain path {y}")',
])
def test_the_guard_leaves_safe_lines_alone(line):
    assert not _offences(line, "x.py")
