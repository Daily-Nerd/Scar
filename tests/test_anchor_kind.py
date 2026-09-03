"""What kind of anchor fired, recorded on the row (#294).

A command firing and an edit firing went through one writer with one row
shape, so `edits_observed` counted both. That number is the denominator of
`injection_rate`, and commands can only ever add hits to the numerator: the
command path returns early on no match, so it never contributes a zero-hit
row even with SCAR_LOG_ZERO_HITS=1.

Same absence convention as armed_ids and demotion_reasons (#266): a MISSING
`anchor_kind` means the row predates the field, never "edit".
"""

from __future__ import annotations

import io
import json

import pytest

from scar.cli import _aggregate_firings, _stage_block, _stage_lines, main
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
  - issue: 294
status: active
---

Do not lower the sleep.
"""

COMMAND_SCAR = r"""---
id: 2
type: deadend
title: Bare uv sync strips extras
severity: high
confidence: 0.9
created: 2026-09-02
authors: [mara]
anchors:
  - command: 'uv sync(?!.* --all-extras)'
evidence:
  - issue: 294
status: active
---

Always run uv sync --all-extras.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-sleep.fence.md").write_text(FENCE)
    (tmp_path / ".scars" / "0002-uv.deadend.md").write_text(COMMAND_SCAR)
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


# --- what every writer stamps on the row ----------------------------------

def test_edit_precheck_stamps_edit(repo, monkeypatch, capsys):
    feed(monkeypatch, {"tool_use_id": "e1",
                       "tool_input": {"file_path": str(repo / "payments" / "a.py"),
                                      "content": "lower the sleep to 3\n"}})
    assert main(["hook", "precheck"]) == 0
    capsys.readouterr()
    assert [r["anchor_kind"] for r in rows(repo)] == ["edit"]


def test_command_precheck_stamps_command(repo, monkeypatch, capsys):
    feed(monkeypatch, {"tool_use_id": "c1", "cwd": str(repo),
                       "tool_input": {"command": "uv sync"}})
    assert main(["hook", "precheck-command"]) == 0
    capsys.readouterr()
    assert [r["anchor_kind"] for r in rows(repo)] == ["command"]


def test_zero_hit_edit_row_is_still_an_edit(repo, monkeypatch, capsys):
    """The zero-hit row IS the denominator (#217). If it carried no kind it
    would land in kind-unknown and take the denominator with it."""
    monkeypatch.setenv("SCAR_LOG_ZERO_HITS", "1")
    feed(monkeypatch, {"tool_use_id": "e2",
                       "tool_input": {"file_path": str(repo / "docs.txt"),
                                      "content": "nothing here\n"}})
    assert main(["hook", "precheck"]) == 0
    capsys.readouterr()
    logged = rows(repo)
    assert logged and logged[0]["scar_ids"] == []
    assert logged[0]["anchor_kind"] == "edit"


def test_codex_edit_stamps_edit(repo, monkeypatch, capsys):
    patch = ("*** Begin Patch\n*** Update File: payments/a.py\n@@\n"
             "+lower the sleep to 3\n*** End Patch")
    feed(monkeypatch, {"cwd": str(repo), "hook_event_name": "PreToolUse",
                       "tool_name": "apply_patch", "tool_use_id": "x1",
                       "tool_input": {"command": patch}})
    assert main(["hook", "codex-pretool"]) == 0
    capsys.readouterr()
    assert [r["anchor_kind"] for r in rows(repo)] == ["edit"]


def test_codex_command_stamps_command(repo, monkeypatch, capsys):
    feed(monkeypatch, {"cwd": str(repo), "hook_event_name": "PreToolUse",
                       "tool_name": "Bash", "tool_use_id": "x2",
                       "tool_input": {"command": "uv sync"}})
    assert main(["hook", "codex-pretool"]) == 0
    capsys.readouterr()
    assert [r["anchor_kind"] for r in rows(repo)] == ["command"]


def test_cascade_edit_stamps_edit(repo, monkeypatch, capsys):
    feed(monkeypatch, {"agent_action_name": "pre_write_code",
                       "trajectory_id": "t1",
                       "tool_info": {"file_path": str(repo / "payments" / "a.py"),
                                     "edits": [{"old_string": "x = 1",
                                                "new_string": "lower the sleep to 3"}]}})
    main(["cascade-hook"])
    capsys.readouterr()
    assert [r["anchor_kind"] for r in rows(repo)] == ["edit"]


def test_cascade_command_stamps_command(repo, monkeypatch, capsys):
    feed(monkeypatch, {"agent_action_name": "pre_run_command",
                       "trajectory_id": "t1",
                       "tool_info": {"command_line": "uv sync", "cwd": str(repo)}})
    main(["cascade-hook"])
    capsys.readouterr()
    assert [r["anchor_kind"] for r in rows(repo)] == ["command"]


def test_writer_omits_the_key_when_no_kind_is_given(tmp_path, monkeypatch):
    """Omitted, never defaulted to "edit". A writer that does not know which
    it was must say so, and "edit" is the flattering guess."""
    from scar.hooks import _log_firing
    from scar.store import ScarStore, init_scars
    monkeypatch.setenv("SCAR_STATE_DIR", str(tmp_path / "state"))
    init_scars(tmp_path)
    _log_firing(ScarStore.discover(tmp_path), "src/a.py", [])
    log = tmp_path / "state" / "firing-log.jsonl"
    assert "anchor_kind" not in json.loads(log.read_text().strip())


# --- the aggregator -------------------------------------------------------

def _row(**kw):
    rec = {"repo": "/r", "ts": "2026-01-01T00:00:00", "scar_ids": [1],
           "count": 1, "anchor_kind": "edit"}
    rec.update(kw)
    return rec


def _edits_and_commands():
    """The issue's reproduction: two real edits, one of them a zero-hit, and
    eight command firings."""
    return ([_row(scar_ids=[1], matched={"total": 2, "content": 1, "path_only": 1}),
             _row(scar_ids=[], count=0)]
            + [_row(anchor_kind="command", scar_ids=[2],
                    matched={"total": 1, "content": 1, "path_only": 0})
               for _ in range(8)])


def test_command_rows_do_not_inflate_the_edit_denominator():
    agg = _aggregate_firings(_edits_and_commands())
    assert agg["edits_observed"] == 2
    assert agg["command_firings"] == 8
    assert agg["injection_rate"] == 0.5


def test_command_rows_do_not_flatter_the_precision_proxies():
    """Command matches are content-signal by construction, so every one of
    them drags path_only_ratio toward zero and cofires_per_edit toward one."""
    agg = _aggregate_firings(_edits_and_commands())
    assert agg["census_known"] == 1
    assert agg["cofires_per_edit"] == 2.0
    assert agg["path_only_ratio"] == 0.5


def test_a_command_firing_still_counts_for_the_scar_that_fired():
    """Only the EDIT denominators change. A scar that fired on a command
    fired, and its per-scar count must say so."""
    agg = _aggregate_firings(_edits_and_commands())
    assert agg["counts"][2] == 8
    assert agg["total"] == 9


def test_rows_without_the_key_are_unknown_not_edits():
    rec = _row()
    del rec["anchor_kind"]
    agg = _aggregate_firings([rec, rec])
    assert agg["firings_kind_unknown"] == 2
    assert agg["edits_observed"] == 0
    assert agg["command_firings"] == 0


def test_injection_rate_is_refused_when_every_row_is_kind_unknown():
    """Not 1.0, not 0.0. An empty denominator has no rate, and #217 already
    settled that the answer there is null."""
    hit, miss = _row(), _row(scar_ids=[], count=0)
    del hit["anchor_kind"]
    del miss["anchor_kind"]
    agg = _aggregate_firings([hit, miss])
    assert agg["injection_rate"] is None
    assert agg["edits_observed"] == 0
    assert agg["firings_kind_unknown"] == 2


def test_kind_unknown_rows_still_prove_the_precheck_hook_ran():
    """The disconnected detector asks whether precheck recorded ANYTHING
    (#237). A legacy log full of kind-unknown rows is an old log, not a dead
    hook, and raising that alarm on it would be a false positive."""
    rec = _row()
    del rec["anchor_kind"]
    agg = _aggregate_firings([rec, {"repo": "/r", "ts": "2026-01-02T00:00:00",
                                    "violation_ids": [1], "count": 1,
                                    "target": "payments/a.py"}])
    assert agg["instrument_disconnected"] is False


def test_only_command_rows_still_prove_the_precheck_hook_ran():
    agg = _aggregate_firings([_row(anchor_kind="command"),
                              {"repo": "/r", "ts": "2026-01-02T00:00:00",
                               "violation_ids": [1], "count": 1,
                               "target": "payments/a.py"}])
    assert agg["instrument_disconnected"] is False


# --- what the reader is told ---------------------------------------------

def _stage_text(records):
    agg = _aggregate_firings(records)
    return " ".join(_stage_lines(_stage_block(agg), agg["per_scar"], agg["total"]))


def test_render_names_command_firings():
    text = _stage_text(_edits_and_commands())
    assert "8 command firing(s)" in text
    assert "2 observed edit(s)" in text


def test_render_names_kind_unknown_rows():
    rec = _row()
    del rec["anchor_kind"]
    assert "1 row(s) of unknown anchor kind" in _stage_text([rec])


def test_render_stays_quiet_when_every_row_is_an_edit():
    text = _stage_text([_row()])
    assert "command firing" not in text
    assert "unknown anchor kind" not in text
