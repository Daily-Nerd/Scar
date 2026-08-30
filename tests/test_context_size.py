"""Context size on the firing row (#279).

Recording only. No buckets, no thresholds, no claims — the pre-registered
analysis is #282 and is deliberately gated on this having produced a real
distribution first.
"""

from __future__ import annotations

import json

import pytest

from scar.hooks import CONTEXT_BYTES_KEY, _context_bytes


def _scar_text(scar_id=1):
    return ("---\n"
            f"id: {scar_id}\ntype: landmine\ntitle: scar {scar_id}\n"
            "severity: medium\nconfidence: 0.7\n"
            "anchors:\n  - path: src/\n"
            "evidence:\n  - issue: 279\nstatus: active\n---\n\nBody.\n")


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


# --- the measurement itself -----------------------------------------------

def test_reads_the_transcript_size(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("x" * 4096)
    assert _context_bytes({"transcript_path": str(t)}) == 4096


def test_absent_key_is_none_not_zero(tmp_path):
    # Zero would say "the context was empty", which is a claim. None says
    # "this host did not tell us", which is the truth.
    assert _context_bytes({}) is None


def test_unstatable_transcript_is_none(tmp_path):
    assert _context_bytes({"transcript_path": str(tmp_path / "missing.jsonl")}) is None


def test_non_string_transcript_path_is_none():
    # landmine #12 tolerance: any shape can arrive on a hook payload.
    for bad in (None, 3, [], {}, ""):
        assert _context_bytes({"transcript_path": bad}) is None


def test_measurement_never_raises(monkeypatch, tmp_path):
    """Hot path. A stat failure must degrade to unknown, never propagate."""
    import os
    monkeypatch.setattr(os, "stat", lambda *a, **k: (_ for _ in ()).throw(OSError))
    assert _context_bytes({"transcript_path": str(tmp_path / "t")}) is None


# --- what reaches the row -------------------------------------------------

def _fire(monkeypatch, repo, extra=None):
    from scar import hooks
    root, state = repo
    payload = {"tool_use_id": "e1",
               "tool_input": {"file_path": str(root / "src" / "a.py"),
                              "content": "x\n"}}
    payload.update(extra or {})
    monkeypatch.setattr(hooks, "_read_payload", lambda: payload)
    hooks.precheck()
    return _rows(state)


def test_row_carries_the_size_when_the_host_supplies_it(repo, monkeypatch, capsys):
    root, _state = repo
    t = root / "t.jsonl"
    t.write_text("y" * 1234)
    rows = _fire(monkeypatch, repo, {"transcript_path": str(t)})
    capsys.readouterr()
    assert rows and rows[0][CONTEXT_BYTES_KEY] == 1234


def test_key_is_OMITTED_when_the_host_supplies_nothing(repo, monkeypatch, capsys):
    """Absent key means unknown. Writing 0, or writing null, would both be
    read as a measurement by a consumer that does not know better — the same
    mistake armed_ids and block_capable each had to avoid."""
    rows = _fire(monkeypatch, repo)
    capsys.readouterr()
    assert rows and CONTEXT_BYTES_KEY not in rows[0]


def test_a_broken_transcript_path_omits_rather_than_zeroes(repo, monkeypatch, capsys):
    root, _state = repo
    rows = _fire(monkeypatch, repo, {"transcript_path": str(root / "nope.jsonl")})
    capsys.readouterr()
    assert rows and CONTEXT_BYTES_KEY not in rows[0]


def test_context_size_does_not_gate_injection(repo, monkeypatch, capsys):
    """Recording only. Nothing about the size may change what is injected."""
    root, _state = repo
    big = root / "big.jsonl"
    big.write_text("z" * 5_000_000)
    rows = _fire(monkeypatch, repo, {"transcript_path": str(big)})
    out = capsys.readouterr().out
    assert rows[0]["scar_ids"] == [1]
    assert "scar" in out.lower()


# --- is the signal actually there? make it measurable, not assumed --------

def _agg(records):
    from scar.cli import _aggregate_firings
    return _aggregate_firings(records)


def _row(**kw):
    rec = {"repo": "/r", "ts": "2026-01-01T00:00:00", "scar_ids": [1],
           "count": 1}
    rec.update(kw)
    return rec


def test_population_rate_is_reported_not_assumed():
    """Whether a host supplies transcript_path on the injection hook is not
    verifiable from here. Shipping a field that may never populate without
    reporting its population rate is how a blind column goes unnoticed."""
    agg = _agg([_row(context_bytes=100), _row()])
    assert agg["firings_context_known"] == 1
    assert agg["firings_context_unknown"] == 1


def test_context_counters_share_the_total_firings_denominator():
    agg = _agg([_row(scar_ids=[1, 2], count=2, context_bytes=10), _row()])
    assert (agg["firings_context_known"] + agg["firings_context_unknown"]
            == agg["total"] == 3)


def test_a_bool_is_not_a_size():
    # json.loads gives True for `true`, and bool is an int subclass, so a
    # malformed row would otherwise count as a real measurement.
    agg = _agg([_row(context_bytes=True)])
    assert agg["firings_context_known"] == 0
    assert agg["firings_context_unknown"] == 1
