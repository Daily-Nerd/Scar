"""Which anchor kinds matched each scar, recorded on the firing row.

`ScarMatch.matched_by` has always known which kinds hit, but the writer took a
list of scars and the kinds were dropped before the log. Without them a row
cannot say whether a scar fired on its path, its symbol or its content.

Same absence convention as armed_ids and anchor_kind (#266): a MISSING
`matched_by` means the row predates the field, never "unknown kinds".
"""

from __future__ import annotations

import io
import json

import pytest

from scar.cli import main
from scar.store import init_scars

FENCE = r"""---
id: 1
type: fence
title: Sleep is 7s for vendor window
severity: critical
confidence: 0.9
created: 2026-09-02
authors: [mara]
anchors:
  - path: payments/
  - pattern: 'lower.{0,20}sleep'
evidence:
  - issue: 1
status: active
---

Do not lower the sleep.
"""

SECOND = r"""---
id: 2
type: fence
title: Retries are capped
severity: high
confidence: 0.9
created: 2026-09-02
authors: [mara]
anchors:
  - pattern: 'max_retries\s*=\s*9'
evidence:
  - issue: 1
status: active
---

Do not raise the cap.
"""

COMMAND_SCAR = r"""---
id: 3
type: deadend
title: Bare uv sync strips extras
severity: high
confidence: 0.9
created: 2026-09-02
authors: [mara]
anchors:
  - command: 'uv sync(?!.* --all-extras)'
evidence:
  - issue: 1
status: active
---

Always run uv sync --all-extras.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-sleep.fence.md").write_text(FENCE)
    (tmp_path / ".scars" / "0002-retries.fence.md").write_text(SECOND)
    (tmp_path / ".scars" / "0003-uv.deadend.md").write_text(COMMAND_SCAR)
    (tmp_path / "payments").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def feed(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def rows(repo):
    log = repo / "state" / "firing-log.jsonl"
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text().splitlines() if x.strip()]


def test_precheck_records_kinds_for_every_matched_scar(repo, monkeypatch, capsys):
    feed(monkeypatch, {"tool_use_id": "e1",
                       "tool_input": {
                           "file_path": str(repo / "payments" / "a.py"),
                           "content": "lower the sleep to 3\nmax_retries = 9\n"}})
    assert main(["hook", "precheck"]) == 0
    capsys.readouterr()
    row = rows(repo)[0]
    by = row["matched_by"]
    assert set(by) == {str(i) for i in row["scar_ids"]} == {"1", "2"}
    assert all(isinstance(k, str) for k in by)
    assert "content_pattern" in by["1"] and "path" in by["1"]
    assert by["2"] == ["content_pattern"]


def test_command_precheck_records_command_kind(repo, monkeypatch, capsys):
    feed(monkeypatch, {"tool_use_id": "c1", "cwd": str(repo),
                       "tool_input": {"command": "uv sync"}})
    assert main(["hook", "precheck-command"]) == 0
    capsys.readouterr()
    assert rows(repo)[0]["matched_by"] == {"3": ["command"]}


def test_codex_edit_records_kinds(repo, monkeypatch, capsys):
    patch = ("*** Begin Patch\n*** Update File: payments/a.py\n@@\n"
             "+lower the sleep to 3\n*** End Patch")
    feed(monkeypatch, {"cwd": str(repo), "hook_event_name": "PreToolUse",
                       "tool_name": "apply_patch", "tool_use_id": "x1",
                       "tool_input": {"command": patch}})
    assert main(["hook", "codex-pretool"]) == 0
    capsys.readouterr()
    assert "content_pattern" in rows(repo)[0]["matched_by"]["1"]


def test_cascade_edit_records_kinds(repo, monkeypatch, capsys):
    feed(monkeypatch, {"agent_action_name": "pre_write_code",
                       "trajectory_id": "t1",
                       "tool_info": {"file_path": str(repo / "payments" / "a.py"),
                                     "edits": [{"old_string": "x = 1",
                                                "new_string": "lower the sleep to 3"}]}})
    main(["cascade-hook"])
    capsys.readouterr()
    row = rows(repo)[0]
    assert set(row["matched_by"]) == {str(i) for i in row["scar_ids"]}
    assert "content_pattern" in row["matched_by"]["1"]


def test_writer_omits_the_key_without_matches(tmp_path, monkeypatch):
    """No match objects in hand means the key is OMITTED, not `{}`: an empty
    object would state that the fired scars matched on nothing."""
    from scar.hooks import _log_firing
    from scar.store import ScarStore
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    init_scars(tmp_path)
    _log_firing(ScarStore.discover(tmp_path), "src/a.py", [])
    log = tmp_path / "state" / "firing-log.jsonl"
    assert "matched_by" not in json.loads(log.read_text().strip())


def test_writer_serialises_given_kinds(tmp_path, monkeypatch):
    from scar.hooks import _log_firing
    from scar.store import ScarStore
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    init_scars(tmp_path)
    _log_firing(ScarStore.discover(tmp_path), "src/a.py", [],
                matched_by={"24": ["path", "symbol"], "1": ["content_pattern"]})
    log = tmp_path / "state" / "firing-log.jsonl"
    assert json.loads(log.read_text().strip())["matched_by"] == {
        "24": ["path", "symbol"], "1": ["content_pattern"]}


def test_stats_reader_accepts_rows_with_and_without_the_key(tmp_path):
    from scar.cli import _aggregate_firings
    base = {"ts": "2026-09-02T10:00:00", "repo": str(tmp_path), "target": "a.py",
            "scar_ids": [1], "count": 1, "anchor_kind": "edit"}
    with_key = dict(base, matched_by={"1": ["path", "content_pattern"]})
    a = _aggregate_firings([base])
    b = _aggregate_firings([with_key])
    assert a == b
