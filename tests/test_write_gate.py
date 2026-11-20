"""One write gate for active scar files (#412)."""
import ast
import base64
import json
import shutil
import stat
from pathlib import Path

import pytest

from scar import renames
from scar.model import parse_scar_text
from scar.record import record_hash
from scar.store import ScarStore, SignedRecordWouldChange, init_scars, write_active_scar

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


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def make_act(inputs_hash):
    payload = base64.urlsafe_b64encode(
        json.dumps({"inputs_hash": inputs_hash}).encode()).rstrip(b"=").decode()
    return f"h.{payload}.s"


@pytest.fixture
def repo(tmp_path):
    init_scars(tmp_path)
    return tmp_path


def write_scar(repo, n=1, *, act="matching", anchor="src/x.py", symbol=None):
    """act: None (unsigned), 'matching', 'stale' (hash of another record), or
    a raw string (used as the act text, e.g. a malformed one)."""
    lines = [f"id: {n}", "type: fence", f"title: Scar {n}", "severity: low",
             "confidence: 0.5", "created: 2026-11-01", 'authors: ["claude-code"]',
             "promoted_by: kibukx"]
    tail = ["anchors:", f"  - path: {anchor}"]
    if symbol:
        tail.append(f"  - symbol: {symbol}")
    tail.append("status: active")
    p = repo / ".scars" / f"{n:04d}-s{n}.fence.md"

    def write(extra):
        p.write_text("---\n" + "\n".join(lines + extra + tail) + "\n---\n\nBody.\n")

    write([])
    if act is not None:
        if act == "matching":
            act_text = make_act(record_hash(parse_scar_text(p.read_text())))
        elif act == "stale":
            act_text = make_act("uNOTTHISRECORD")
        else:
            act_text = act
        write([f"promotion_act: {act_text}"])
    return p


def test_signed_scar_anchor_rewrite_is_refused_and_leaves_bytes_alone(repo):
    p = write_scar(repo, 7)
    before = p.read_bytes()
    with pytest.raises(SignedRecordWouldChange) as exc:
        renames.apply_anchor_rewrite(p, "path", {"src/x.py": "src/y.py"})
    assert exc.value.scar_id == 7
    assert "scar 7" in str(exc.value) and "signed" in str(exc.value)
    assert p.read_bytes() == before


def test_unsigned_scar_anchor_rewrite_is_written(repo):
    p = write_scar(repo, 1, act=None)
    assert renames.apply_anchor_rewrite(p, "path", {"src/x.py": "src/y.py"}) is True
    assert "- path: src/y.py" in p.read_text()


def test_signed_scar_transition_and_note_are_written_and_act_still_matches(repo):
    p = write_scar(repo, 1)
    store = ScarStore(repo, repo / ".scars")
    store.append_evidence_note(1, "note: a")
    store.transition(1, "challenged", "why", "2026-11-20")
    scar = parse_scar_text(p.read_text())
    assert scar.status == "challenged"
    assert scar.promotion_act
    from scar import presence
    assert presence.act_inputs_hash(scar.promotion_act) == record_hash(scar)


@pytest.mark.parametrize("act", ["stale", "not-a-jws"])
def test_already_stale_or_malformed_act_scar_stays_archivable(repo, act):
    p = write_scar(repo, 1, act=act)
    ScarStore(repo, repo / ".scars").transition(1, "archived", "done", "2026-11-20")
    assert parse_scar_text(p.read_text()).status == "archived"


@pytest.mark.parametrize("act", ["stale", "not-a-jws"])
def test_already_stale_or_malformed_act_scar_anchor_rewrite_still_writes(repo, act):
    p = write_scar(repo, 1, act=act)
    assert renames.apply_anchor_rewrite(p, "path", {"src/x.py": "src/y.py"}) is True


def test_dropping_the_act_line_while_the_record_is_unchanged_is_refused(repo):
    p = write_scar(repo, 3)
    before = p.read_bytes()
    new_text = "\n".join(
        line for line in p.read_text().split("\n") if not line.startswith("promotion_act:"))
    with pytest.raises(SignedRecordWouldChange):
        write_active_scar(p, new_text)
    assert p.read_bytes() == before


def test_altering_the_act_line_while_the_record_is_unchanged_is_refused(repo):
    p = write_scar(repo, 3)
    before = p.read_bytes()
    old_act = parse_scar_text(p.read_text()).promotion_act
    with pytest.raises(SignedRecordWouldChange):
        write_active_scar(p, p.read_text().replace(old_act, make_act("uOTHER")))
    assert p.read_bytes() == before


def test_unparseable_new_text_over_a_matching_act_is_refused(repo):
    p = write_scar(repo, 3)
    before = p.read_bytes()
    with pytest.raises(SignedRecordWouldChange):
        write_active_scar(p, "not a scar at all")
    assert p.read_bytes() == before


def test_a_write_that_keeps_act_and_record_goes_through(repo):
    p = write_scar(repo, 3)
    write_active_scar(p, p.read_text().replace("confidence: 0.5", "confidence: 0.9"))
    assert "confidence: 0.9" in p.read_text()


def test_the_write_is_atomic_and_keeps_the_file_mode(repo):
    p = write_scar(repo, 1, act=None)
    p.chmod(0o640)
    write_active_scar(p, p.read_text().replace("confidence: 0.5", "confidence: 0.9"))
    assert stat.S_IMODE(p.stat().st_mode) == 0o640
    assert [f.name for f in p.parent.iterdir() if f.name.startswith(".")] == []


def test_an_unparseable_file_on_disk_is_overwritten_with_the_new_text(repo):
    p = write_scar(repo, 1)
    new_text = write_scar(repo, 2, act=None).read_text()
    p.write_text("not a scar, no frontmatter at all\n")
    before = sorted(f.name for f in p.parent.iterdir())
    write_active_scar(p, new_text)
    assert p.read_text() == new_text
    assert sorted(f.name for f in p.parent.iterdir()) == before


@pytest.mark.parametrize("failing", ["os.replace", "shutil.copymode"])
def test_a_failed_write_raises_as_is_and_leaves_file_and_directory_alone(
        repo, monkeypatch, failing):
    p = write_scar(repo, 1, act=None)
    original = p.read_bytes()
    before = sorted(f.name for f in p.parent.iterdir())
    boom = OSError("disk says no")

    def fail(*args, **kwargs):
        raise boom

    monkeypatch.setattr(f"scar.store.{failing}", fail)
    with pytest.raises(OSError) as excinfo:
        write_active_scar(p, original.decode().replace("confidence: 0.5", "confidence: 0.9"))
    assert excinfo.value is boom
    assert p.read_bytes() == original
    assert sorted(f.name for f in p.parent.iterdir()) == before


def test_a_symlinked_scar_file_stays_a_symlink(repo, tmp_path_factory):
    target = tmp_path_factory.mktemp("elsewhere") / "real.md"
    p = write_scar(repo, 1, act=None)
    target.write_bytes(p.read_bytes())
    p.unlink()
    p.symlink_to(target)
    write_active_scar(p, target.read_text().replace("confidence: 0.5", "confidence: 0.9"))
    assert p.is_symlink() and "confidence: 0.9" in target.read_text()


# ---------------------------------------------------------------------------
# Guard: no fourth direct writer
# ---------------------------------------------------------------------------
SRC = Path(__file__).resolve().parent.parent / "src" / "scar"

# Every file-writing call site under src/scar, counted per module. Each entry
# is a write to something other than an active scar file, or the gate itself:
#   store.py     gate (fdopen + os.replace), init README/keep, candidate write,
#                the exclusive create in commit_promotion (the signing path)
#   cli.py       init example seed, a log append, candidate write
#   mcp.py       candidate write (scar_draft)
#   gc / cascade atomic log and state replace (fdopen + os.replace each)
#   hooks.py     log appends
#   installer.py hook and settings files
# renames.py is absent on purpose: it writes only through the gate. If this
# test fails, the new write either targets an active scar file, in which case
# it must go through store.write_active_scar, or it targets something else and
# the table below gets one more line saying what.
ALLOWED_WRITE_SITES = {
    "cascade.py": 2,
    "cli.py": 3,
    "gc.py": 2,
    "hooks.py": 3,
    "installer.py": 8,
    "mcp.py": 1,
    "store.py": 6,
}

_WRITE_ATTRS = {"write_text", "write_bytes"}
_WRITE_FUNCS = {("os", "replace"), ("os", "rename"), ("shutil", "copyfile"),
                ("shutil", "copy"), ("shutil", "copy2"), ("shutil", "move")}


def _mode_writes(call):
    mode = None
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and any(c in mode for c in "wax+")


def _write_sites():
    sites = {}
    for path in sorted(SRC.glob("*.py")):
        count = 0
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute):
                owner = fn.value.id if isinstance(fn.value, ast.Name) else None
                if fn.attr in _WRITE_ATTRS or (owner, fn.attr) in _WRITE_FUNCS:
                    count += 1
                elif fn.attr in ("open", "fdopen") and _mode_writes(
                        node if owner in ("os",) else _shift(node)):
                    count += 1
            elif isinstance(fn, ast.Name) and fn.id == "open" and _mode_writes(node):
                count += 1
        if count:
            sites[path.name] = count
    return sites


def _shift(call):
    """Path.open(mode) takes the mode first; present it like open(path, mode)."""
    shifted = ast.Call(func=call.func, args=[ast.Constant(None), *call.args],
                       keywords=call.keywords)
    return shifted


def test_no_module_writes_a_file_outside_the_known_sites():
    assert _write_sites() == ALLOWED_WRITE_SITES


def test_renames_has_no_write_of_its_own():
    assert "renames.py" not in _write_sites()
