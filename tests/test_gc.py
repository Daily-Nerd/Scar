"""gc core — machine-state cleanup + read-only repo reporting (#115).

Posture under test: machine state (state_dir: markers, firing log) -> gc
ACTS. Repo state (.scars/: candidates, fp-log.txt) -> gc REPORTS, never
writes. The structural guarantee test at the bottom hashes every .scars/
file gc can see, runs every gc entry point, and asserts nothing changed.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from scar import gc
from scar.store import ScarStore, init_scars

DAY = 86400


def _touch(path: Path, *, age_days: float = 0.0, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    mtime = time.time() - age_days * DAY
    os.utime(path, (mtime, mtime))
    return path


# --- prune_markers ---------------------------------------------------------

def test_prune_markers_removes_old_keeps_fresh(tmp_path):
    state = tmp_path / "state"
    old = _touch(state / "drafted-s1", age_days=10)
    fresh = _touch(state / "drafted-s2", age_days=1)

    removed = gc.prune_markers(state, 7)

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()


def test_prune_markers_boundary_just_under_days_kept(tmp_path):
    """'older than N days' is exclusive: a marker just under N days old is
    NOT removed. (Exact equality with the N-day boundary is inherently
    racy — gc's own `time.time()` call runs slightly after the test's — so
    the boundary is exercised with a small safety margin instead.)"""
    state = tmp_path / "state"
    just_under = _touch(state / "drafted-s3", age_days=6.9)

    removed = gc.prune_markers(state, 7)

    assert removed == []
    assert just_under.exists()


def test_prune_markers_boundary_just_over_days_removed(tmp_path):
    state = tmp_path / "state"
    just_over = _touch(state / "drafted-s4", age_days=7.1)

    removed = gc.prune_markers(state, 7)

    assert removed == [just_over]
    assert not just_over.exists()


def test_prune_markers_dry_run_removes_nothing(tmp_path):
    state = tmp_path / "state"
    old = _touch(state / "drafted-s1", age_days=10)

    removed = gc.prune_markers(state, 7, dry_run=True)

    assert removed == [old]
    assert old.exists()


def test_prune_markers_missing_state_dir_returns_empty(tmp_path):
    assert gc.prune_markers(tmp_path / "nope", 7) == []


def test_prune_markers_ignores_non_marker_files(tmp_path):
    state = tmp_path / "state"
    unrelated = _touch(state / "firing-log.jsonl", age_days=30)
    drafter_log = _touch(state / "drafter-log.jsonl", age_days=30)

    removed = gc.prune_markers(state, 7)

    assert removed == []
    assert unrelated.exists()
    assert drafter_log.exists()


# --- truncate_firing_log ----------------------------------------------------

def test_truncate_firing_log_keeps_newest(tmp_path):
    log = tmp_path / "firing-log.jsonl"
    lines = [f'{{"n": {i}}}' for i in range(20)]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    dropped = gc.truncate_firing_log(log, 5)

    assert dropped == 15
    kept = log.read_text(encoding="utf-8").splitlines()
    assert kept == lines[-5:]


def test_truncate_firing_log_dry_run_no_write(tmp_path):
    log = tmp_path / "firing-log.jsonl"
    lines = [f'{{"n": {i}}}' for i in range(20)]
    original = "\n".join(lines) + "\n"
    log.write_text(original, encoding="utf-8")

    dropped = gc.truncate_firing_log(log, 5, dry_run=True)

    assert dropped == 15
    assert log.read_text(encoding="utf-8") == original


def test_truncate_firing_log_absent_log_no_op(tmp_path):
    log = tmp_path / "firing-log.jsonl"
    assert gc.truncate_firing_log(log, 5) == 0
    assert not log.exists()


def test_truncate_firing_log_short_log_no_op(tmp_path):
    log = tmp_path / "firing-log.jsonl"
    original = "\n".join(f'{{"n": {i}}}' for i in range(3)) + "\n"
    log.write_text(original, encoding="utf-8")

    assert gc.truncate_firing_log(log, 10) == 0
    assert log.read_text(encoding="utf-8") == original


def test_truncate_firing_log_malformed_lines_counted_not_parsed(tmp_path):
    """Tail-by-line, not tail-by-record: a corrupt line still counts and
    truncation still works — no JSON parsing required."""
    log = tmp_path / "firing-log.jsonl"
    lines = ["not json at all", '{"n": 1}', "{{{broken", '{"n": 2}', '{"n": 3}']
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    dropped = gc.truncate_firing_log(log, 2)

    assert dropped == 3
    assert log.read_text(encoding="utf-8").splitlines() == lines[-2:]


def test_truncate_to_zero_empties_log(tmp_path):
    """max_entries=0 should empty the log entirely."""
    log = tmp_path / "firing-log.jsonl"
    log.write_text('{"a": 1}\n{"b": 2}\n{"c": 3}\n', encoding="utf-8")

    dropped = gc.truncate_firing_log(log, 0)

    assert dropped == 3
    assert log.read_text(encoding="utf-8") == ""


def test_truncate_firing_log_atomic_no_temp_file_left(tmp_path):
    log = tmp_path / "firing-log.jsonl"
    lines = [f'{{"n": {i}}}' for i in range(20)]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    gc.truncate_firing_log(log, 5)

    leftovers = [p for p in tmp_path.iterdir() if p.name != log.name]
    assert leftovers == []


# --- candidate_ages / fp_log_report (read-only reports) --------------------

def test_candidate_ages_oldest_first(tmp_path):
    init_scars(tmp_path)
    store = ScarStore(root=tmp_path, scars_dir=tmp_path / ".scars")
    cand_dir = store.scars_dir / "candidates"
    _touch(cand_dir / "young.md", age_days=1, content="---\n---\n")
    _touch(cand_dir / "old.md", age_days=30, content="---\n---\n")

    ages = gc.candidate_ages(store)

    assert [e["name"] for e in ages] == ["old.md", "young.md"]
    assert ages[0]["age_days"] > ages[1]["age_days"]


def test_candidate_ages_empty_when_none(tmp_path):
    init_scars(tmp_path)
    store = ScarStore(root=tmp_path, scars_dir=tmp_path / ".scars")
    assert gc.candidate_ages(store) == []


def test_fp_log_report_absent(tmp_path):
    init_scars(tmp_path)
    store = ScarStore(root=tmp_path, scars_dir=tmp_path / ".scars")
    report = gc.fp_log_report(store)
    assert report == {"present": False, "size": 0, "lines": 0}


def test_fp_log_report_present_size_and_lines(tmp_path):
    init_scars(tmp_path)
    store = ScarStore(root=tmp_path, scars_dir=tmp_path / ".scars")
    fp_log = store.scars_dir / "candidates" / "fp-log.txt"
    fp_log.write_text("2026-06-10 false trigger\n2026-06-11 also false\n", encoding="utf-8")

    report = gc.fp_log_report(store)

    assert report["present"] is True
    assert report["lines"] == 2
    assert report["size"] == fp_log.stat().st_size


# --- structural guarantee: .scars/ is never touched -------------------------

def _hash_dir(path: Path) -> dict[str, str]:
    out = {}
    for f in sorted(path.rglob("*")):
        if f.is_file():
            out[str(f.relative_to(path))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def test_gc_never_mutates_scars_dir(tmp_path):
    init_scars(tmp_path)
    store = ScarStore(root=tmp_path, scars_dir=tmp_path / ".scars")
    cand_dir = store.scars_dir / "candidates"
    _touch(cand_dir / "a.md", age_days=5, content="---\n---\nbody\n")
    fp_log = store.scars_dir / "candidates" / "fp-log.txt"
    fp_log.write_text("2026-06-10 false trigger\n", encoding="utf-8")

    before = _hash_dir(store.scars_dir)

    state = tmp_path / "state"
    _touch(state / "drafted-old", age_days=10)
    log = tmp_path / "state" / "firing-log.jsonl"
    log.write_text("\n".join(f'{{"n": {i}}}' for i in range(20)) + "\n", encoding="utf-8")

    gc.prune_markers(state, 7)
    gc.truncate_firing_log(log, 5)
    gc.candidate_ages(store)
    gc.fp_log_report(store)

    after = _hash_dir(store.scars_dir)
    assert before == after
