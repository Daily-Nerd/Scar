"""Evidence reachability — RED→GREEN per test.

A scar's commit-SHA evidence is a receipt; it must resolve from HEAD or it
can't be verified in a fresh clone (scar #5). These tests use real git repos
because reachability is a git fact, not a content fact.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from scar.store import ScarStore, init_scars
from scar.evidence import UnreachableEvidence, unreachable_evidence


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _scar(*, id: int, evidence: list[str]) -> str:
    ev = "".join(f"  - {e}\n" for e in evidence)
    return (
        f"---\n"
        f"id: {id}\n"
        f"type: deadend\n"
        f"title: evidence test {id}\n"
        f"severity: medium\n"
        f"confidence: 0.8\n"
        f"created: 2026-01-01\n"
        f"authors: [test]\n"
        f"anchors:\n"
        f"  - path: src/\n"
        f"evidence:\n"
        f"{ev}"
        f"status: active\n"
        f"---\n\nBody.\n"
    )


def _git(tmp: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tmp), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _init_git(tmp: Path) -> None:
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t.t")
    _git(tmp, "config", "user.name", "t")


def _commit(tmp: Path, name: str, content: str = "x") -> str:
    (tmp / name).write_text(content)
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", name)
    return _git(tmp, "rev-parse", "HEAD")


def _store_with_scar(tmp: Path, scar_text: str) -> ScarStore:
    init_scars(tmp)
    (tmp / ".scars" / "0001-x.deadend.md").write_text(scar_text)
    store = ScarStore.discover(tmp)
    assert store is not None
    return store


# ---------------------------------------------------------------------------
# TEST 1: a commit SHA reachable from HEAD → no warning
# ---------------------------------------------------------------------------

def test_reachable_sha_no_warning(tmp_path):
    _init_git(tmp_path)
    sha = _commit(tmp_path, "a.py")
    store = _store_with_scar(tmp_path, _scar(id=1, evidence=[f"commit: {sha}"]))
    assert unreachable_evidence(store, tmp_path) == []


# ---------------------------------------------------------------------------
# TEST 2: a SHA that does not exist in the repo → flagged "missing"
# ---------------------------------------------------------------------------

def test_missing_sha_flagged(tmp_path):
    _init_git(tmp_path)
    _commit(tmp_path, "a.py")
    store = _store_with_scar(tmp_path, _scar(id=1, evidence=["commit: deadbeef"]))
    findings = unreachable_evidence(store, tmp_path)
    assert findings == [UnreachableEvidence(scar_id=1, sha="deadbeef", reason="missing")]


# ---------------------------------------------------------------------------
# TEST 3: a SHA that exists but is NOT an ancestor of HEAD → "off-history"
# ---------------------------------------------------------------------------

def test_offhistory_sha_flagged(tmp_path):
    _init_git(tmp_path)
    first = _commit(tmp_path, "a.py")
    stranded = _commit(tmp_path, "b.py")     # HEAD now at stranded
    _git(tmp_path, "reset", "--hard", first)  # HEAD back to first; stranded dangles
    store = _store_with_scar(tmp_path, _scar(id=1, evidence=[f"commit: {stranded}"]))
    findings = unreachable_evidence(store, tmp_path)
    assert len(findings) == 1
    assert findings[0].scar_id == 1
    assert findings[0].reason == "off-history"


# ---------------------------------------------------------------------------
# TEST 4: non-commit evidence (pr, note) is never checked
# ---------------------------------------------------------------------------

def test_non_commit_evidence_ignored(tmp_path):
    _init_git(tmp_path)
    _commit(tmp_path, "a.py")
    store = _store_with_scar(tmp_path, _scar(
        id=1, evidence=["pr: 40", 'note: "history rewritten at release"']))
    assert unreachable_evidence(store, tmp_path) == []


# ---------------------------------------------------------------------------
# TEST 5: shallow clone → check skipped (returns None), no false warnings
# ---------------------------------------------------------------------------

def test_shallow_clone_skips(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _init_git(origin)
    old = _commit(origin, "a.py")
    _commit(origin, "b.py")  # HEAD; 'old' is now history

    dest = tmp_path / "shallow"
    subprocess.run(["git", "clone", "--depth", "1", "-q",
                    origin.as_uri(), str(dest)], check=True)
    # cite the old SHA that a depth-1 clone does not have
    store = _store_with_scar(dest, _scar(id=1, evidence=[f"commit: {old}"]))
    assert unreachable_evidence(store, dest) is None
