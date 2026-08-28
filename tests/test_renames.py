"""Git rename following (#109) — RED->GREEN per test.

Detection is READ-ONLY (whole-history -M walk); the fix path is a surgical
single-line text rewrite, never a parse+reserialize (landmine #4: promote's
roundtrip once silently dropped expires/evidence on a full reserialize).
"""

from __future__ import annotations

import subprocess
from pathlib import Path



def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")


def _write(repo: Path, rel: str, content: str = "x = 1\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


# ---------------------------------------------------------------------------
# build_rename_map / resolve_rename — the pure resolution logic
# ---------------------------------------------------------------------------

def test_simple_rename_resolves(tmp_path):
    from scar.renames import build_rename_map, resolve_rename

    _init_repo(tmp_path)
    _write(tmp_path, "src/old.py")
    _commit(tmp_path, "add old.py")
    _git(tmp_path, "mv", "src/old.py", "src/new.py")
    _commit(tmp_path, "rename old to new")

    rmap = build_rename_map(tmp_path)
    assert rmap is not None
    target = resolve_rename(rmap, "src/old.py", {"src/new.py"})
    assert target == "src/new.py"


def test_chained_rename_resolves_to_final_target(tmp_path):
    from scar.renames import build_rename_map, resolve_rename

    _init_repo(tmp_path)
    _write(tmp_path, "src/a.py")
    _commit(tmp_path, "add a.py")
    _git(tmp_path, "mv", "src/a.py", "src/b.py")
    _commit(tmp_path, "rename a to b")
    _git(tmp_path, "mv", "src/b.py", "src/c.py")
    _commit(tmp_path, "rename b to c")

    rmap = build_rename_map(tmp_path)
    target = resolve_rename(rmap, "src/a.py", {"src/c.py"})
    assert target == "src/c.py"


def test_deleted_not_renamed_resolves_to_none(tmp_path):
    from scar.renames import build_rename_map, resolve_rename

    _init_repo(tmp_path)
    _write(tmp_path, "src/gone.py")
    _commit(tmp_path, "add gone.py")
    (tmp_path / "src" / "gone.py").unlink()
    _commit(tmp_path, "delete gone.py")

    rmap = build_rename_map(tmp_path)
    target = resolve_rename(rmap, "src/gone.py", set())
    assert target is None


def test_ambiguous_rename_resolves_to_none(tmp_path):
    from scar.renames import build_rename_map, resolve_rename

    _init_repo(tmp_path)
    _write(tmp_path, "src/dup.py", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
    _commit(tmp_path, "add dup.py")
    _git(tmp_path, "mv", "src/dup.py", "src/one.py")
    _commit(tmp_path, "rename dup to one")
    # a SECOND, unrelated file that also gets renamed FROM the same original
    # name on another branch-equivalent history point — simulate ambiguity by
    # recreating dup.py with similar content and renaming it elsewhere too.
    _git(tmp_path, "mv", "src/one.py", "src/dup.py")
    _commit(tmp_path, "rename back to dup")
    _git(tmp_path, "mv", "src/dup.py", "src/two.py")
    _commit(tmp_path, "rename dup to two")

    rmap = build_rename_map(tmp_path)
    # src/dup.py was recorded as renamed to BOTH src/one.py and src/two.py
    # across history — two distinct targets from the same origin -> ambiguous.
    target = resolve_rename(rmap, "src/dup.py", {"src/two.py"})
    assert target is None


def test_untracked_target_resolves_to_none(tmp_path):
    from scar.renames import build_rename_map, resolve_rename

    _init_repo(tmp_path)
    _write(tmp_path, "src/old.py")
    _commit(tmp_path, "add old.py")
    _git(tmp_path, "mv", "src/old.py", "src/new.py")
    _commit(tmp_path, "rename old to new")
    (tmp_path / "src" / "new.py").unlink()
    _commit(tmp_path, "delete new.py")

    rmap = build_rename_map(tmp_path)
    # new.py is no longer tracked -> even though the rename is unambiguous,
    # it must not be reported as a valid fix target.
    target = resolve_rename(rmap, "src/old.py", set())
    assert target is None


def test_non_git_dir_returns_none(tmp_path):
    from scar.renames import build_rename_map

    # tmp_path is not a git repo at all.
    assert build_rename_map(tmp_path) is None


def test_shallow_clone_returns_none(tmp_path):
    from scar.renames import build_rename_map

    origin = tmp_path / "origin"
    _init_repo(origin)
    _write(origin, "a.py")
    _commit(origin, "init")
    _write(origin, "b.py")
    _commit(origin, "second")  # HEAD; gives --depth 1 something to land on

    dest = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--depth", "1", "-q",
                    origin.as_uri(), str(dest)], check=True)
    assert build_rename_map(dest) is None


# ---------------------------------------------------------------------------
# is_concrete_path_anchor — directory / glob anchors are out of scope (#109)
# ---------------------------------------------------------------------------

def test_directory_anchor_is_not_concrete():
    from scar.renames import is_concrete_path_anchor
    assert is_concrete_path_anchor("src/module/") is False


def test_glob_anchor_is_not_concrete():
    from scar.renames import is_concrete_path_anchor
    assert is_concrete_path_anchor("src/*.py") is False


def test_plain_file_anchor_is_concrete():
    from scar.renames import is_concrete_path_anchor
    assert is_concrete_path_anchor("src/old.py") is True


# ---------------------------------------------------------------------------
# RenameResolver — the lazy/cached wrapper detect_orphans/detect_partial_rot use
# ---------------------------------------------------------------------------

def test_resolver_resolves_dead_concrete_anchor_only(tmp_path):
    from scar.renames import RenameResolver

    _init_repo(tmp_path)
    _write(tmp_path, "src/old.py")
    _commit(tmp_path, "add old.py")
    _git(tmp_path, "mv", "src/old.py", "src/new.py")
    _commit(tmp_path, "rename old to new")

    resolver = RenameResolver(tmp_path)
    out = resolver.resolve(["src/old.py", "src/some_dir/"], {"src/new.py"})
    assert out == {"src/old.py": "src/new.py"}


def test_resolver_none_repo_returns_empty():
    from scar.renames import RenameResolver
    resolver = RenameResolver(None)
    assert resolver.resolve(["src/old.py"], {"src/new.py"}) == {}


def test_resolver_only_builds_map_once(tmp_path, monkeypatch):
    from scar import renames as renames_mod
    from scar.renames import RenameResolver

    _init_repo(tmp_path)
    _write(tmp_path, "src/old.py")
    _commit(tmp_path, "add old.py")
    _git(tmp_path, "mv", "src/old.py", "src/new.py")
    _commit(tmp_path, "rename old to new")

    calls = []
    real = renames_mod.build_rename_map

    def _counting(repo):
        calls.append(repo)
        return real(repo)

    monkeypatch.setattr(renames_mod, "build_rename_map", _counting)
    resolver = RenameResolver(tmp_path)
    resolver.resolve(["src/old.py"], {"src/new.py"})
    resolver.resolve(["src/old.py"], {"src/new.py"})
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# apply_rename_fix — the surgical single-line write path
# ---------------------------------------------------------------------------

_SCAR_TEXT = """\
---
id: 42
type: deadend
title: test scar
severity: medium
confidence: 0.8
created: 2026-01-01
authors: [test]
anchors:
  - path: src/old.py
evidence:
  - commit: abc1234
expires:
  condition: "code is deleted"
  review_after: 2027-01-01
status: active
---

Body text with weird "quotes" and trailing whitespace.
"""


def test_apply_rename_fix_rewrites_only_the_anchor_line(tmp_path):
    from scar.renames import apply_rename_fix

    f = tmp_path / "scar.md"
    f.write_text(_SCAR_TEXT)
    before = f.read_text()

    changed = apply_rename_fix(f, {"src/old.py": "src/new.py"})
    assert changed is True

    after = f.read_text()
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    assert len(before_lines) == len(after_lines)

    diffs = [(i, b, a) for i, (b, a) in enumerate(zip(before_lines, after_lines)) if b != a]
    assert len(diffs) == 1
    i, b, a = diffs[0]
    assert b == "  - path: src/old.py"
    assert a == "  - path: src/new.py"


def test_apply_rename_fix_no_match_returns_false(tmp_path):
    from scar.renames import apply_rename_fix

    f = tmp_path / "scar.md"
    f.write_text(_SCAR_TEXT)
    before = f.read_text()
    changed = apply_rename_fix(f, {"src/nonexistent.py": "src/new.py"})
    assert changed is False
    assert f.read_text() == before


# ---------------------------------------------------------------------------
# apply_anchor_rewrite (#111) — kind-aware generalization of the surgical
# writer above. apply_rename_fix must delegate to it and stay byte-identical
# (the tests above are that contract, unchanged).
# ---------------------------------------------------------------------------

_SCAR_TEXT_SYMBOL = """\
---
id: 43
type: deadend
title: test scar with symbol anchor
severity: medium
confidence: 0.8
created: 2026-01-01
authors: [test]
anchors:
  - path: src/old.py
  - symbol: old_helper
evidence:
  - commit: abc1234
expires:
  condition: "code is deleted"
  review_after: 2027-01-01
status: active
---

Body text with weird "quotes" and trailing whitespace.
"""


def test_apply_anchor_rewrite_path_kind_rewrites_only_the_anchor_line(tmp_path):
    from scar.renames import apply_anchor_rewrite

    f = tmp_path / "scar.md"
    f.write_text(_SCAR_TEXT)
    before = f.read_text()

    changed = apply_anchor_rewrite(f, "path", {"src/old.py": "src/new.py"})
    assert changed is True

    after = f.read_text()
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    assert len(before_lines) == len(after_lines)

    diffs = [(i, b, a) for i, (b, a) in enumerate(zip(before_lines, after_lines)) if b != a]
    assert len(diffs) == 1
    i, b, a = diffs[0]
    assert b == "  - path: src/old.py"
    assert a == "  - path: src/new.py"


def test_apply_anchor_rewrite_symbol_kind_rewrites_only_the_symbol_line(tmp_path):
    from scar.renames import apply_anchor_rewrite

    f = tmp_path / "scar.md"
    f.write_text(_SCAR_TEXT_SYMBOL)
    before = f.read_text()

    changed = apply_anchor_rewrite(f, "symbol", {"old_helper": "new_helper"})
    assert changed is True

    after = f.read_text()
    before_lines = before.split("\n")
    after_lines = after.split("\n")
    assert len(before_lines) == len(after_lines)

    diffs = [(i, b, a) for i, (b, a) in enumerate(zip(before_lines, after_lines)) if b != a]
    assert len(diffs) == 1
    i, b, a = diffs[0]
    assert b == "  - symbol: old_helper"
    assert a == "  - symbol: new_helper"
    # the path anchor on the same file must be untouched
    assert "  - path: src/old.py" in after_lines


def test_apply_anchor_rewrite_symbol_kind_leaves_path_lines_alone(tmp_path):
    from scar.renames import apply_anchor_rewrite

    f = tmp_path / "scar.md"
    f.write_text(_SCAR_TEXT_SYMBOL)
    before = f.read_text()

    # A rename map keyed by the PATH value must not match under "symbol" kind.
    changed = apply_anchor_rewrite(f, "symbol", {"src/old.py": "src/new.py"})
    assert changed is False
    assert f.read_text() == before


def test_apply_anchor_rewrite_unknown_kind_returns_false(tmp_path):
    from scar.renames import apply_anchor_rewrite

    f = tmp_path / "scar.md"
    f.write_text(_SCAR_TEXT)
    before = f.read_text()

    changed = apply_anchor_rewrite(f, "pattern", {"src/old.py": "src/new.py"})
    assert changed is False
    assert f.read_text() == before


def test_apply_rename_fix_delegates_to_apply_anchor_rewrite(tmp_path, monkeypatch):
    from scar import renames as renames_mod

    calls = []
    real = renames_mod.apply_anchor_rewrite

    def _spy(path, kind, renamed):
        calls.append(kind)
        return real(path, kind, renamed)

    monkeypatch.setattr(renames_mod, "apply_anchor_rewrite", _spy)
    f = tmp_path / "scar.md"
    f.write_text(_SCAR_TEXT)
    changed = renames_mod.apply_rename_fix(f, {"src/old.py": "src/new.py"})
    assert changed is True
    assert calls == ["path"]
