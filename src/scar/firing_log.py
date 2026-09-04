"""The one reader for ``firing-log.jsonl``.

The log is append-only JSONL written best-effort from a hook that must never
fail or delay an edit, so any line shape can and does appear: ``null``, ``[]``,
numbers, truncated JSON, non-string ``ts``. Landmine #12 records three separate
bugs shipped from that fact in two days (PR #122, issue #124), and prescribes
two rules that every reader has to follow identically:

* guard every line with a BLANKET ``except Exception``, never a narrow tuple,
  because a reader that escapes into precheck's outer fail-open kills injection
  permanently and silently;
* distrust slice math, because ``lines[-0:]`` is the whole list rather than
  nothing.

Both rules used to be re-implemented per reader. They live here instead. This
module deliberately imports nothing from ``hooks`` or ``cli`` so either can
call it: the caller supplies the path (``hooks.firing_log_path()`` still owns
where the log lives, including the ``SCAR_STATE_DIR`` override).
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["read_firing_log_records"]


def read_firing_log_records(path: Path, *, tail: int | None = None) -> list[dict]:
    """Every well-formed dict record in the firing log, oldest first.

    Lines that are blank, unparseable, or parse to a non-dict are dropped.
    ``tail`` keeps only the newest N LINES, applied before parsing, so a
    malformed line still consumes one of the N slots exactly as it counts
    toward ``gc.truncate_firing_log``'s total.

    Never raises on log content or on an unreadable log: a missing file, a
    directory in its place, and undecodable bytes all read as "no records".
    That is required, not merely tidy, because one caller runs inside the
    fail-open precheck path.

    A negative ``tail`` IS refused, because it is a caller bug rather than a
    data condition: ``lines[-(-1):]`` silently returns the OLDEST rows, the
    opposite of what every caller wants.
    """
    if tail is not None and tail < 0:
        raise ValueError(f"tail must be non-negative, got {tail}")

    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []

    lines = text.splitlines()
    if tail is not None:
        # Not lines[-tail:] — at tail == 0 that is lines[-0:], the whole list.
        lines = lines[-tail:] if tail > 0 else []

    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        records.append(rec)
    return records
