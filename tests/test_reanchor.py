"""Reanchor — orphan recovery v1 (#111). RED->GREEN per test.

Fixture idiom mirrors test_renames.py: a real tmp git repo, `_init_repo` /
`_write` / `_commit` helpers, byte-diff assertions for the write path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scar import symbols

symbols_extra = pytest.mark.skipif(
    not symbols.symbols_available(), reason="tree-sitter extra not installed")


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
# trace_dead_path — killing-commit overlap + bounded pickaxe fallback
# ---------------------------------------------------------------------------

def test_same_commit_move_proposes_high_confidence(tmp_path):
    from scar.reanchor import trace_dead_path

    _init_repo(tmp_path)
    old = "src/old_module.py"
    content = ("def hello():\n    return 'world from old module'\n\n"
               "def helper():\n    return 42\n")
    _write(tmp_path, old, content)
    _commit(tmp_path, "add old_module")
    (tmp_path / old).unlink()
    _write(tmp_path, "src/new_module.py", content)
    _commit(tmp_path, "move old_module to new_module")

    results = trace_dead_path(tmp_path, old, {"src/new_module.py"})
    assert len(results) == 1
    path, ratio, evidence = results[0]
    assert path == "src/new_module.py"
    assert ratio >= 0.6
    assert "killing-commit" in evidence


def test_later_commit_move_found_via_pickaxe(tmp_path):
    from scar.reanchor import trace_dead_path

    _init_repo(tmp_path)
    old = "src/old_module.py"
    content = ("def hello():\n    return 'a very distinctive marker string here'\n\n"
               "def helper():\n    return 99\n")
    _write(tmp_path, old, content)
    _commit(tmp_path, "add old_module")
    (tmp_path / old).unlink()
    _commit(tmp_path, "delete old_module")
    _write(tmp_path, "README.md", "unrelated intervening change\n")
    _commit(tmp_path, "unrelated change")
    _write(tmp_path, "src/new_module.py", content)
    _commit(tmp_path, "add new_module elsewhere")

    tracked = {"src/new_module.py", "README.md"}
    results = trace_dead_path(tmp_path, old, tracked)
    paths = {p for p, _, _ in results}
    assert "src/new_module.py" in paths
    match = next(r for r in results if r[0] == "src/new_module.py")
    assert "pickaxe" in match[2]


def test_truly_deleted_no_proposal(tmp_path):
    from scar.reanchor import trace_dead_path

    _init_repo(tmp_path)
    old = "src/gone.py"
    _write(tmp_path, old, "def gone():\n    return 'nothing like this exists elsewhere'\n")
    _commit(tmp_path, "add gone")
    (tmp_path / old).unlink()
    _commit(tmp_path, "delete gone")
    _write(tmp_path, "src/unrelated.py",
           "def unrelated():\n    return 'totally different content here'\n")
    _commit(tmp_path, "add unrelated")

    results = trace_dead_path(tmp_path, old, {"src/unrelated.py"})
    assert results == []


def test_ambiguous_two_targets_both_proposed(tmp_path):
    from scar.reanchor import trace_dead_path

    _init_repo(tmp_path)
    old = "src/shared.py"
    content = ("def shared():\n    return 'duplicated content across two files'\n\n"
               "def more():\n    return 7\n")
    _write(tmp_path, old, content)
    _commit(tmp_path, "add shared")
    (tmp_path / old).unlink()
    _write(tmp_path, "src/copy_one.py", content)
    _write(tmp_path, "src/copy_two.py", content)
    _commit(tmp_path, "delete shared, duplicate into two files")

    tracked = {"src/copy_one.py", "src/copy_two.py"}
    results = trace_dead_path(tmp_path, old, tracked)
    assert {p for p, _, _ in results} == {"src/copy_one.py", "src/copy_two.py"}


def test_non_git_dir_returns_empty(tmp_path):
    from scar.reanchor import trace_dead_path

    results = trace_dead_path(tmp_path, "src/x.py", set())
    assert results == []


def test_shallow_clone_returns_empty(tmp_path):
    from scar.reanchor import trace_dead_path

    origin = tmp_path / "origin"
    _init_repo(origin)
    _write(origin, "a.py")
    _commit(origin, "init")
    _write(origin, "b.py")
    _commit(origin, "second")

    dest = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--depth", "1", "-q",
                    origin.as_uri(), str(dest)], check=True)
    results = trace_dead_path(dest, "a.py", {"b.py"})
    assert results == []


# ---------------------------------------------------------------------------
# propose_path_reanchors — the ReanchorProposal wrapper
# ---------------------------------------------------------------------------

def test_propose_path_reanchors_wraps_with_scar_id(tmp_path):
    from scar.reanchor import propose_path_reanchors

    _init_repo(tmp_path)
    old = "src/old_module.py"
    content = ("def hello():\n    return 'world from old module'\n\n"
               "def helper():\n    return 42\n")
    _write(tmp_path, old, content)
    _commit(tmp_path, "add old_module")
    (tmp_path / old).unlink()
    _write(tmp_path, "src/new_module.py", content)
    _commit(tmp_path, "move old_module to new_module")

    proposals = propose_path_reanchors(tmp_path, scar_id=7, dead_anchor=old,
                                       tracked={"src/new_module.py"})
    assert len(proposals) == 1
    p = proposals[0]
    assert p.scar_id == 7
    assert p.anchor_kind == "path"
    assert p.dead_anchor == old
    assert p.proposed_anchor == "src/new_module.py"
    assert p.confidence == "high"
    assert 0.0 <= p.signal <= 1.0


# ---------------------------------------------------------------------------
# Symbol tracing (#111 commit 2) — [symbols]-gated: fingerprint the dead
# symbol at its evidence SHA, scan the current tree via _walk_defs, rank by
# jaccard + same-name bonus.
# ---------------------------------------------------------------------------

_OLD_BODY = (
    "def old_helper(items):\n"
    "    total = 0\n"
    "    for item in items:\n"
    "        if item > 0:\n"
    "            total += item\n"
    "    return total\n"
)

_NEW_NAME_SAME_SHAPE = (
    "def new_helper(items):\n"
    "    total = 0\n"
    "    for item in items:\n"
    "        if item > 0:\n"
    "            total += item\n"
    "    return total\n"
)

_UNRELATED_BODY = "def unrelated():\n    pass\n"


@symbols_extra
def test_symbol_renamed_same_file_high_confidence(tmp_path):
    from scar.model import Scar
    from scar.reanchor import trace_dead_symbol

    _init_repo(tmp_path)
    path = "src/pkg/mod.py"
    _write(tmp_path, path, _OLD_BODY)
    _commit(tmp_path, "add old_helper")
    evidence_sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()

    _write(tmp_path, path, _NEW_NAME_SAME_SHAPE)
    _commit(tmp_path, "rename old_helper to new_helper")

    scar = Scar(id=1, symbol_anchors=["old_helper"], path_anchors=[path])
    results = trace_dead_symbol(tmp_path, scar, "old_helper", evidence_sha, {path})
    assert results
    proposed, score, evidence = results[0]
    assert proposed == f"{path}::new_helper"
    assert score >= 0.7


@symbols_extra
def test_symbol_relocated_to_new_file(tmp_path):
    from scar.model import Scar
    from scar.reanchor import trace_dead_symbol

    _init_repo(tmp_path)
    old_path = "src/pkg/mod.py"
    new_path = "src/lib/other.py"
    _write(tmp_path, old_path, _OLD_BODY)
    _commit(tmp_path, "add old_helper")
    evidence_sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()

    _write(tmp_path, old_path, _UNRELATED_BODY)  # old_helper gone from here
    _write(tmp_path, new_path, _NEW_NAME_SAME_SHAPE)  # moved + renamed elsewhere
    _commit(tmp_path, "relocate old_helper")

    scar = Scar(id=2, symbol_anchors=["old_helper"], path_anchors=[old_path])
    tracked = {old_path, new_path}
    results = trace_dead_symbol(tmp_path, scar, "old_helper", evidence_sha, tracked)
    assert any(p == f"{new_path}::new_helper" for p, _, _ in results)


@symbols_extra
def test_symbol_truly_unresolvable_no_proposal(tmp_path):
    from scar.model import Scar
    from scar.reanchor import trace_dead_symbol

    _init_repo(tmp_path)
    path = "src/pkg/mod.py"
    _write(tmp_path, path, _OLD_BODY)
    _commit(tmp_path, "add old_helper")
    evidence_sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()

    _write(tmp_path, path, _UNRELATED_BODY)
    _commit(tmp_path, "replace old_helper with something unrelated")

    scar = Scar(id=3, symbol_anchors=["old_helper"], path_anchors=[path])
    results = trace_dead_symbol(tmp_path, scar, "old_helper", evidence_sha, {path})
    assert results == []


def test_symbol_tracing_degrades_without_extra(tmp_path, monkeypatch):
    from scar import reanchor as reanchor_mod
    from scar.model import Scar

    monkeypatch.setattr(reanchor_mod.symbols, "symbols_available", lambda: False)
    scar = Scar(id=4, symbol_anchors=["foo"], path_anchors=["a.py"])
    assert reanchor_mod.trace_dead_symbol(tmp_path, scar, "foo", "deadbeef", {"a.py"}) == []
    assert reanchor_mod.dead_symbol_anchors(scar, tmp_path, {"a.py"}) == []
    assert reanchor_mod.propose_symbol_reanchors_for_scar(tmp_path, scar, {"a.py"}) == []


@symbols_extra
def test_dead_symbol_anchors_detects_gone_symbol(tmp_path):
    from scar.model import Scar
    from scar.reanchor import dead_symbol_anchors

    _init_repo(tmp_path)
    path = "src/pkg/mod.py"
    _write(tmp_path, path, _NEW_NAME_SAME_SHAPE)  # only new_helper exists
    _commit(tmp_path, "add new_helper")

    scar = Scar(id=5, symbol_anchors=["old_helper"], path_anchors=[path])
    dead = dead_symbol_anchors(scar, tmp_path, {path})
    assert dead == ["old_helper"]

    scar_live = Scar(id=6, symbol_anchors=["new_helper"], path_anchors=[path])
    assert dead_symbol_anchors(scar_live, tmp_path, {path}) == []


@symbols_extra
def test_propose_symbol_reanchors_for_scar_orchestrates_evidence_sha(tmp_path):
    from scar.model import Scar
    from scar.reanchor import propose_symbol_reanchors_for_scar

    _init_repo(tmp_path)
    path = "src/pkg/mod.py"
    _write(tmp_path, path, _OLD_BODY)
    _commit(tmp_path, "add old_helper")
    evidence_sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()

    _write(tmp_path, path, _NEW_NAME_SAME_SHAPE)
    _commit(tmp_path, "rename old_helper to new_helper")

    scar = Scar(id=7, symbol_anchors=["old_helper"], path_anchors=[path],
               evidence=[f"commit: {evidence_sha}"])
    proposals = propose_symbol_reanchors_for_scar(tmp_path, scar, {path})
    assert len(proposals) == 1
    p = proposals[0]
    assert p.scar_id == 7
    assert p.anchor_kind == "symbol"
    assert p.dead_anchor == "old_helper"
    assert p.proposed_anchor == f"{path}::new_helper"
    assert p.confidence == "high"


# ---------------------------------------------------------------------------
# format_reanchor_note: the evidence-note text `reanchor --apply` records
# on a scar (#296). Pure formatting, no git needed.
# ---------------------------------------------------------------------------

def test_format_reanchor_note_path_anchor():
    from scar.reanchor import format_reanchor_note

    note = format_reanchor_note(
        date="2026-09-02", anchor_kind="path",
        old="src/old.py", new="src/new.py", confidence="high")
    assert note == (
        'note: "reanchored 2026-09-02: path src/old.py -> src/new.py (tier: high)"')


def test_format_reanchor_note_symbol_anchor_uses_kind_word():
    from scar.reanchor import format_reanchor_note

    note = format_reanchor_note(
        date="2026-09-02", anchor_kind="symbol",
        old="old_helper", new="src/pkg/mod.py::new_helper", confidence="high")
    assert note == (
        'note: "reanchored 2026-09-02: symbol old_helper -> '
        'src/pkg/mod.py::new_helper (tier: high)"')
