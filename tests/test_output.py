"""The 3-way output dispatch: JSON, Rich-tty, plain non-tty.

The non-tty branch must call the plain renderer verbatim (byte-preserving for
CI/substring consumers); Rich must NEVER render there. Only the tty branch uses
Rich. JSON short-circuits both.
"""

import json

from scar import output


def test_json_flag_emits_indented_json_and_skips_renderers(capsys):
    calls = []
    output.render(
        data={"a": 1, "b": ["x"]},
        json_flag=True,
        tty=lambda: calls.append("tty"),
        plain=lambda: calls.append("plain"),
    )
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1, "b": ["x"]}
    assert "\n  " in out  # indent=2 pretty print
    assert calls == []  # neither renderer ran


def test_non_tty_calls_plain_renderer_only(monkeypatch, capsys):
    monkeypatch.setattr(output, "is_tty", lambda: False)
    calls = []
    output.render(
        data={"a": 1},
        json_flag=False,
        tty=lambda: calls.append("tty"),
        plain=lambda: calls.append("plain"),
    )
    assert calls == ["plain"]


def test_tty_calls_tty_renderer_only(monkeypatch):
    monkeypatch.setattr(output, "is_tty", lambda: True)
    calls = []
    output.render(
        data={"a": 1},
        json_flag=False,
        tty=lambda: calls.append("tty"),
        plain=lambda: calls.append("plain"),
    )
    assert calls == ["tty"]
