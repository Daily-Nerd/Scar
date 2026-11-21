"""The one owner of "replace a file atomically" (#430).

Standard library only and no package imports, so the hook hot path can pull it
in without paying for the store, the CLI or rich.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def atomic_write_text(path: Path | str, text: str, *,
                      copymode_from: Path | str | None = None) -> None:
    """Replace ``path`` with ``text`` so a reader sees the old file or the new
    one, never a partial write.

    The text goes to a uniquely named temp file in the same directory (the
    swap must not cross a filesystem), is written as UTF-8 with no newline
    translation, then swapped in with os.replace. ``copymode_from`` copies that
    file's permission bits onto the temp file first.

    On any failure, KeyboardInterrupt included: the descriptor is closed, the
    temp file is removed, and the original error propagates. Removing the temp
    file is best effort and never replaces that error. No locking: two writers
    race, and the last replace wins."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=f".{path.name}.", suffix=".tmp")
    try:
        try:
            fh = os.fdopen(fd, "w", encoding="utf-8", newline="")
        except BaseException:
            os.close(fd)
            raise
        with fh:
            fh.write(text)
        if copymode_from is not None:
            shutil.copymode(copymode_from, tmp_name)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
