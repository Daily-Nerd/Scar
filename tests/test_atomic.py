"""atomic_write_text: the one owner of the temp-file-then-os.replace rule (#430)."""
import ast
import os
import stat
from pathlib import Path

import pytest

from scar import atomic
from scar.atomic import atomic_write_text

SRC = Path(__file__).resolve().parent.parent / "src" / "scar"


def names(directory):
    return sorted(p.name for p in directory.iterdir())


def test_replaces_content_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert names(tmp_path) == ["state.json"]


def test_creates_a_missing_target(tmp_path):
    target = tmp_path / "fresh.txt"
    atomic_write_text(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert names(tmp_path) == ["fresh.txt"]


def test_text_is_written_byte_for_byte_utf8_without_newline_translation(tmp_path):
    target = tmp_path / "scar.md"
    text = "line one\r\nline two é\r\n"
    atomic_write_text(target, text)
    assert target.read_bytes() == text.encode("utf-8")


def test_mode_is_copied_only_when_asked(tmp_path):
    target = tmp_path / "scar.md"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o640)
    atomic_write_text(target, "new", copymode_from=target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    other = tmp_path / "other.txt"
    other.write_text("x", encoding="utf-8")
    other.chmod(0o600)
    atomic_write_text(other, "y")
    assert stat.S_IMODE(other.stat().st_mode) == 0o600  # mkstemp default


@pytest.mark.parametrize("failing", ["write", "copymode", "replace"])
def test_a_failure_leaves_target_alone_and_raises_the_original_error(
        tmp_path, monkeypatch, failing):
    target = tmp_path / "scar.md"
    target.write_text("old", encoding="utf-8")
    boom = OSError("disk says no")

    def fail(*args, **kwargs):
        raise boom

    if failing == "write":
        monkeypatch.setattr(atomic.os, "fdopen", fail)
    elif failing == "copymode":
        monkeypatch.setattr(atomic.shutil, "copymode", fail)
    else:
        monkeypatch.setattr(atomic.os, "replace", fail)
    with pytest.raises(OSError) as excinfo:
        atomic_write_text(target, "new", copymode_from=target)
    assert excinfo.value is boom
    assert target.read_text(encoding="utf-8") == "old"
    assert names(tmp_path) == ["scar.md"]


def test_a_failing_write_call_closes_the_descriptor_and_removes_the_temp(
        tmp_path, monkeypatch):
    target = tmp_path / "t.txt"
    boom = OSError("short write")

    class Exploding:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, text):
            raise boom

    real_fdopen = os.fdopen
    opened = []

    def fake_fdopen(fd, *a, **k):
        opened.append(fd)
        real_fdopen(fd, *a, **k).close()
        return Exploding()

    monkeypatch.setattr(atomic.os, "fdopen", fake_fdopen)
    with pytest.raises(OSError) as excinfo:
        atomic_write_text(target, "new")
    assert excinfo.value is boom
    assert names(tmp_path) == []


def test_keyboard_interrupt_during_write_leaves_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "t.txt"
    target.write_text("old", encoding="utf-8")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(atomic.os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert names(tmp_path) == ["t.txt"]


def test_a_failed_fdopen_does_not_leak_the_descriptor(tmp_path, monkeypatch):
    seen = []
    real_mkstemp = atomic.tempfile.mkstemp

    def spy(*a, **k):
        fd, name = real_mkstemp(*a, **k)
        seen.append(fd)
        return fd, name

    def fail(*a, **k):
        raise OSError("no fdopen")

    monkeypatch.setattr(atomic.tempfile, "mkstemp", spy)
    monkeypatch.setattr(atomic.os, "fdopen", fail)
    with pytest.raises(OSError, match="no fdopen"):
        atomic_write_text(tmp_path / "t.txt", "x")
    with pytest.raises(OSError):
        os.fstat(seen[0])  # closed
    assert names(tmp_path) == []


def test_a_failed_cleanup_never_hides_the_original_error(tmp_path, monkeypatch):
    target = tmp_path / "t.txt"
    boom = OSError("replace failed")

    def fail_replace(*a, **k):
        raise boom

    def fail_unlink(*a, **k):
        raise PermissionError("cannot remove temp")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)
    monkeypatch.setattr(atomic.os, "unlink", fail_unlink)
    with pytest.raises(OSError) as excinfo:
        atomic_write_text(target, "x")
    assert excinfo.value is boom


def test_temp_names_are_unique_per_call(tmp_path, monkeypatch):
    used = []
    real_replace = os.replace

    def spy(src, dst):
        used.append(src)
        real_replace(src, dst)

    monkeypatch.setattr(atomic.os, "replace", spy)
    atomic_write_text(tmp_path / "t.txt", "a")
    atomic_write_text(tmp_path / "t.txt", "b")
    assert len(set(used)) == 2


def test_the_module_stays_cheap_to_import():
    tree = ast.parse((SRC / "atomic.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "relative")
            assert node.level == 0, "no package-internal imports"
    assert imported <= {"__future__", "os", "shutil", "tempfile", "pathlib"}


def test_replace_and_mkstemp_live_only_in_the_atomic_module():
    """The rule has one owner: any other os.replace or mkstemp in src/scar is a
    fifth hand-rolled copy of it."""
    found = {}
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Attribute)
                    and node.attr in ("replace", "mkstemp")
                    and isinstance(node.value, ast.Name)
                    and node.value.id in ("os", "tempfile")):
                found.setdefault(path.name, []).append(node.attr)
    assert {k: sorted(v) for k, v in found.items()} == {
        "atomic.py": ["mkstemp", "replace"]}
