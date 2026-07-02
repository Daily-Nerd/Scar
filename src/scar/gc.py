"""Machine-state garbage collection (#115).

Posture is the feature: machine state (``~/.claude/scar-state/``, or
``SCAR_STATE_DIR`` — regenerable, one process's local bookkeeping) is
something gc ACTS on. Repo state (``.scars/``, human-gated knowledge) is
something gc REPORTS on and never touches — same ethos as promote/reanchor:
only humans mutate ``.scars/``. Every function below that reads ``.scars/``
opens files with ``read_text`` / ``stat`` only; the structural guarantee test
(``test_gc_never_mutates_scars_dir`` in ``tests/test_gc.py``) hashes the
directory before and after every entry point runs and asserts nothing moved.

Callers resolve the state dir / firing-log path via ``hooks._state_dir()`` /
``hooks.firing_log_path()`` (the ``SCAR_STATE_DIR`` override lives there,
once, module docstring's contract) and pass plain ``Path``s in here — this
module has no opinion on where state lives, only on what to do with it.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from .store import ScarStore

MARKER_GLOB = "drafted-*"
SECONDS_PER_DAY = 86400


def prune_markers(state_dir: Path, days: int, *, dry_run: bool = False) -> list[Path]:
    """Delete stop-drafter ``drafted-<session>`` markers (hooks.py ~194/208)
    whose mtime is older than ``days``. A marker exactly ``days`` old is kept
    — "older than N days" is exclusive, matching the issue wording. Missing
    state dir is a no-op. ``dry_run`` computes the same removal set without
    deleting anything.
    """
    if not state_dir.is_dir():
        return []
    cutoff = time.time() - days * SECONDS_PER_DAY
    removed = []
    for marker in sorted(state_dir.glob(MARKER_GLOB)):
        try:
            mtime = marker.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            removed.append(marker)
            if not dry_run:
                try:
                    marker.unlink()
                except OSError:
                    pass
    return removed


def truncate_firing_log(path: Path, max_entries: int, *, dry_run: bool = False) -> int:
    """Keep only the newest ``max_entries`` lines of the firing log (#106),
    dropping the rest. Returns the number of lines dropped (0 if the log is
    absent or already at/under ``max_entries`` — pure no-op, nothing is
    opened for writing in that case).

    Tail-by-line, not tail-by-record: lines are never JSON-parsed here, so a
    malformed line still counts toward the total and truncation still works.

    Atomic: the replacement content is written to a temp file in the SAME
    directory as ``path``, then swapped in with ``os.replace`` — a
    concurrent precheck append (hooks.py's fail-open ``_log_firing``) can
    never observe, or cause, a partially-written log. The temp file is
    removed on any failure so it never lingers next to the real log.
    """
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= max_entries:
        return 0
    dropped = len(lines) - max_entries
    if dry_run:
        return dropped

    keep = lines[-max_entries:]
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(keep) + "\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return dropped


def candidate_ages(store: ScarStore) -> list[dict]:
    """Candidate funnel report: name + age in days (from file mtime),
    oldest-first. STRICTLY read-only — never writes to ``.scars/``."""
    now = time.time()
    entries = []
    for p in store.candidates():
        try:
            age_days = (now - p.stat().st_mtime) / SECONDS_PER_DAY
        except OSError:
            continue
        entries.append({"name": p.name, "age_days": round(age_days, 1)})
    entries.sort(key=lambda e: -e["age_days"])
    return entries


def fp_log_report(store: ScarStore) -> dict:
    """Presence/size/line-count of ``candidates/fp-log.txt`` — the
    drafter-precision false-positive escape hatch (hooks.py ``stop_drafter``).
    Instrument data for the drafter-precision watch item: reported, never
    auto-cleaned, never written to here."""
    path = store.scars_dir / "candidates" / "fp-log.txt"
    if not path.is_file():
        return {"present": False, "size": 0, "lines": 0}
    try:
        size = path.stat().st_size
        lines = len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return {"present": True, "size": 0, "lines": 0}
    return {"present": True, "size": size, "lines": lines}
