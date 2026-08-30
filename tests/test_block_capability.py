"""Blocking capability must travel with the compliance claim (#278).

"0 violations on a host that could refuse the edit" and "0 violations where
refusing was never possible" are numerically identical and evidentially
different. Nothing in the log distinguished them before this.
"""

from __future__ import annotations

import json

import pytest

from scar.cli import _aggregate_firings


def _row(**kw):
    rec = {"repo": "/r", "ts": "2026-01-01T00:00:00", "scar_ids": [1],
           "count": 1, "armed_ids": [], "verdict_expected": False}
    rec.update(kw)
    return rec


# --- the field is written, never inferred ---------------------------------

def test_capability_is_counted_from_the_row_not_the_runtime_string():
    """Inferring from `runtime` at read time would let a host that gains or
    loses a blocking hook silently rewrite every historical row (#266)."""
    agg = _aggregate_firings([
        _row(runtime="windsurf", block_capable=True),
        _row(runtime="claude-code", block_capable=False),
    ])
    assert agg["firings_block_capable"] == 1
    assert agg["firings_advisory"] == 1


def test_a_runtime_string_alone_proves_nothing():
    # A windsurf row with no capability field is UNKNOWN, not capable.
    agg = _aggregate_firings([_row(runtime="windsurf")])
    assert agg["firings_block_capable"] == 0
    assert agg["firings_block_unknown"] == 1


def test_rows_predating_the_field_are_unknown_not_advisory():
    agg = _aggregate_firings([_row(), _row()])
    assert agg["firings_block_unknown"] == 2
    assert agg["firings_advisory"] == 0


def test_malformed_capability_is_unknown():
    agg = _aggregate_firings([_row(block_capable="yes"),
                              _row(block_capable=None)])
    assert agg["firings_block_unknown"] == 2


# --- the "entirely advisory" claim ----------------------------------------

def test_window_entirely_advisory_is_stated():
    agg = _aggregate_firings([_row(block_capable=False),
                              _row(block_capable=False)])
    assert agg["all_firings_advisory"] is True


def test_one_capable_firing_withdraws_the_claim():
    agg = _aggregate_firings([_row(block_capable=False),
                              _row(block_capable=True)])
    assert agg["all_firings_advisory"] is False


def test_unknown_rows_prevent_the_entirely_claim():
    """"Entirely" is a universal statement. One unplaceable row and it cannot
    be made, in either direction."""
    agg = _aggregate_firings([_row(block_capable=False), _row()])
    assert agg["all_firings_advisory"] is False


def test_no_firings_makes_no_claim():
    assert _aggregate_firings([])["all_firings_advisory"] is False


def test_stage_line_names_the_advisory_window():
    from scar.cli import _stage_block, _stage_lines
    agg = _aggregate_firings([_row(block_capable=False)])
    lines = _stage_lines(_stage_block(agg), agg["per_scar"], agg["total"])
    assert any("advisory" in ln.lower() for ln in lines)


def test_stage_line_reports_the_split_when_mixed():
    from scar.cli import _stage_block, _stage_lines
    agg = _aggregate_firings([_row(block_capable=False),
                              _row(block_capable=True)])
    text = " ".join(_stage_lines(_stage_block(agg), agg["per_scar"], agg["total"]))
    assert "1 firing(s) could refuse the action" in text
    assert "1 advisory" in text


# --- writers --------------------------------------------------------------

def _scar_text(scar_id=1):
    return ("---\n"
            f"id: {scar_id}\ntype: landmine\ntitle: scar {scar_id}\n"
            "severity: high\nconfidence: 0.9\n"
            "anchors:\n  - pattern: \"forbidden_call\"\n"
            "evidence:\n  - issue: 278\nstatus: active\n---\n\nBody.\n")


def _rows(state):
    p = state / "firing-log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] \
        if p.exists() else []


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x\n")
    scars = tmp_path / ".scars"
    scars.mkdir()
    (scars / "0001-a.landmine.md").write_text(_scar_text())
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    return tmp_path, state


def test_claude_code_precheck_records_advisory(repo, monkeypatch, capsys):
    from scar import hooks
    root, state = repo
    target = root / "src" / "a.py"
    monkeypatch.setattr(hooks, "_read_payload", lambda: {
        "tool_use_id": "e1",
        "tool_input": {"file_path": str(target), "content": "forbidden_call()\n"}})
    hooks.precheck()
    capsys.readouterr()
    rows = _rows(state)
    assert rows and rows[0]["block_capable"] is False


def test_cascade_records_capable_because_it_actually_refused(repo, monkeypatch, capsys):
    """Cascade logs a firing ONLY when it blocks: surface-only matches are
    deliberately never logged. So every logged windsurf firing did refuse."""
    from scar import cascade
    root, state = repo
    target = root / "src" / "a.py"
    payload = {"trajectory_id": "t1",
               "tool_info": {"file_path": str(target),
                             "content": "forbidden_call()\n"}}
    rc = cascade.pre_write_code(payload)
    capsys.readouterr()
    assert rc == cascade.BLOCK_EXIT
    rows = _rows(state)
    assert rows and rows[0]["block_capable"] is True
    assert rows[0]["runtime"] == "windsurf"


def test_default_is_advisory_so_a_new_adapter_understates_never_inflates(repo, monkeypatch):
    """The default must fail toward advisory. A forgotten flag then makes the
    claim look WEAKER, which is the safe direction for this metric."""
    import inspect

    from scar.hooks import _log_firing
    assert inspect.signature(_log_firing).parameters["block_capable"].default is False


def test_capability_shares_the_total_firings_denominator():
    """Printed beside total_firings, so it must count the same unit. Counting
    rows while total_firings counts scar-firings puts two units in one
    sentence and reads as though the remainder were known."""
    agg = _aggregate_firings([
        _row(scar_ids=[1, 2, 3], count=3, block_capable=False),
        _row(scar_ids=[4], count=1, block_capable=True),
    ])
    total = (agg["firings_block_capable"] + agg["firings_advisory"]
             + agg["firings_block_unknown"])
    assert total == agg["total"] == 4
