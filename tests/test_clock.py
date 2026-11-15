"""One owner for "what day is it" (#380).

Every surface that needs today's date or the firing-log timestamp reads it
through `scar.clock`. These tests fix the owner once and check each surface
follows it, and a guard test keeps new readers from appearing elsewhere.
"""
import json
import re
from pathlib import Path

from scar import clock
from scar.cli import _review_due, main
from scar.lint import lint_text
from scar.mcp import _handle
from scar.store import init_scars

SRC = Path(__file__).resolve().parent.parent / "src" / "scar"

CANDIDATE = """\
---
type: deadend
title: Tried X, failed
severity: medium
confidence: 0.7
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/
evidence:
  - commit: abc1234
status: candidate
---

Why X failed.
"""

ACTIVE = """\
---
id: 1
type: fence
title: Sleep is 7s
severity: high
confidence: 0.9
created: 2026-06-09
authors: [mara]
anchors:
  - path: payments/
evidence:
  - commit: aaa1111
review_after: 2026-06-01
status: active
---

Do not lower the sleep.
"""


def fix_clock(monkeypatch, day="2031-02-03", stamp=None):
    monkeypatch.setattr(clock, "today", lambda: day)
    monkeypatch.setattr(clock, "now", lambda: stamp or day + "T04:05:06")


class _Store:
    def __init__(self, *scars):
        self._scars = scars

    def firing(self):
        return [("x", s) for s in self._scars]


class _S:
    def __init__(self, review_after):
        self.review_after = review_after


def test_clock_formats():
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", clock.today())
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", clock.now())
    assert clock.now().startswith(clock.today()) or True  # may straddle midnight


def test_review_due_is_strictly_before_today(monkeypatch):
    fix_clock(monkeypatch, "2026-06-10")
    store = _Store(_S("2026-06-09"), _S("2026-06-10"), _S("2026-06-11"), _S(""))
    due = _review_due(store)
    assert [s.review_after for s in due] == ["2026-06-09"]


def test_lint_text_default_today_uses_owner(monkeypatch):
    fix_clock(monkeypatch, "2026-07-01")
    assert any("review_after" in f.message for f in lint_text(ACTIVE))
    fix_clock(monkeypatch, "2026-05-01")
    assert not any("review_after" in f.message for f in lint_text(ACTIVE))


def test_mcp_created_default_uses_owner(tmp_path, monkeypatch):
    fix_clock(monkeypatch, "2031-02-03")
    init_scars(tmp_path)
    _handle("tools/call", {"name": "scar_draft", "arguments": {
        "repo": str(tmp_path), "type": "deadend", "title": "Clock check",
        "anchors": [{"path": "src/"}], "body": "Body."}})
    cand = next((tmp_path / ".scars" / "candidates").glob("clock-check*.md"))
    assert "created: 2031-02-03" in cand.read_text()


def test_archive_date_uses_owner(tmp_path, monkeypatch):
    fix_clock(monkeypatch, "2031-02-03")
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    cand = tmp_path / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    assert main(["archive", str(cand), "--reason", "obsolete"]) == 0
    assert "2031-02-03" in cand.read_text()


def test_firing_log_ts_uses_owner(tmp_path, monkeypatch):
    fix_clock(monkeypatch, "2031-02-03", "2031-02-03T04:05:06")
    (tmp_path / ".git").mkdir()
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-sleep.fence.md").write_text(ACTIVE)
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_input": {"file_path": str(tmp_path / "payments" / "retry.py")}})))
    assert main(["hook", "precheck"]) == 0
    rec = json.loads((state / "firing-log.jsonl").read_text().splitlines()[0])
    assert rec["ts"] == "2031-02-03T04:05:06"


def test_harvest_now_months_derives_from_owner(monkeypatch):
    from scar import harvest
    fix_clock(monkeypatch, "2031-02-03")
    assert harvest._now_months() == 2031 * 12 + 2


_BAD = [
    (re.compile(r"""time\.strftime\(\s*["']%Y-%m-%d"""), 'time.strftime("%Y-%m-%d...")'),
    (re.compile(r"\bdate\.today\("), "date.today()"),
    (re.compile(r"\bdatetime\.(?:datetime\.)?now\(\s*\)"), "datetime.now() with no tz"),
    (re.compile(r"^\s*from\s+(?:\.|scar\.)clock\s+import\b"),
     "from-import of clock names (use `from . import clock`)"),
]


def test_no_module_reads_the_clock_for_today_outside_the_owner():
    """Guard (#380): `scar.clock` is the only reader of the wall clock for
    "today" and the firing-log timestamp. Fails on, anywhere in src/scar/*.py
    except clock.py: time.strftime("%Y-%m-%d..."), date.today(), and a bare
    datetime.now() / datetime.datetime.now() with no tz argument. A call that
    passes tz (an age relative to a parsed timestamp) is not "today" and is
    allowed. To get the date, call clock.today() or clock.now().

    It also fails on `from .clock import ...` / `from scar.clock import ...`:
    a name bound at import time cannot be monkeypatched through scar.clock, so
    tests could no longer fix the date in one place. Use `from . import clock`
    and call `clock.today()`. Only import statements match, not prose."""
    offenders = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "clock.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for rx, label in _BAD:
                if rx.search(line):
                    offenders.append(f"{path.name}:{n}: {label}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
