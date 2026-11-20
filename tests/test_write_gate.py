"""One write gate for active scar files (#412)."""
import shutil
from pathlib import Path

import pytest

from scar.model import parse_scar_text
from scar.record import record_hash
from scar.store import ScarStore

REAL_SCARS = Path(__file__).resolve().parent.parent / ".scars"


def _real_active():
    store = ScarStore(REAL_SCARS.parent, REAL_SCARS)
    return [(f.name, s.id) for f, s in store.active()]


def _copy_repo(tmp_path):
    root = tmp_path / "copy"
    shutil.copytree(REAL_SCARS, root / ".scars", ignore=shutil.ignore_patterns("candidates"))
    return root


def test_real_scar_set_is_not_empty():
    assert len(_real_active()) >= 20


@pytest.mark.parametrize("name,scar_id", _real_active())
def test_transition_and_note_leave_the_record_of_every_real_scar_unchanged(
        tmp_path, name, scar_id):
    """Pin: the parse and reserialize round trip keeps the signed bytes."""
    root = _copy_repo(tmp_path)
    path = root / ".scars" / name
    before = record_hash(parse_scar_text(path.read_text(encoding="utf-8")))
    store = ScarStore(root, root / ".scars")
    store.append_evidence_note(scar_id, "note: pinned 2026-11-20: check")
    assert record_hash(parse_scar_text(path.read_text(encoding="utf-8"))) == before
    store.transition(scar_id, "challenged", "pin", "2026-11-20")
    assert record_hash(parse_scar_text(path.read_text(encoding="utf-8"))) == before
