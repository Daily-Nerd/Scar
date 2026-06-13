"""Orphan detection — RED→GREEN per test.

Fixture pattern mirrors test_match.py: tmp-repo with .git/ + .scars/,
scars written directly as YAML.  git ls-files is simulated by giving the
detector an explicit set of tracked paths via a RepoContext object.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scar.store import ScarStore, init_scars
from scar.orphan import OrphanFinding, anchors_all_dead, detect_orphans

# ---------------------------------------------------------------------------
# Scar text helpers
# ---------------------------------------------------------------------------

def _scar(*, id: int, status: str = "active",
          path_anchors: list[str] = (), pattern_anchors: list[str] = ()) -> str:
    anchor_lines = ""
    for p in path_anchors:
        anchor_lines += f"  - path: {p}\n"
    for pat in pattern_anchors:
        anchor_lines += f'  - pattern: "{pat}"\n'
    return (
        f"---\n"
        f"id: {id}\n"
        f"type: deadend\n"
        f"title: test scar {id}\n"
        f"severity: medium\n"
        f"confidence: 0.8\n"
        f"created: 2026-01-01\n"
        f"authors: [test]\n"
        f"anchors:\n"
        f"{anchor_lines}"
        f"evidence:\n"
        f"  - commit: abc1234\n"
        f"status: {status}\n"
        f"---\n\n"
        f"Body text.\n"
    )


def _make_store(tmp_path: Path, scars: dict[str, str]) -> ScarStore:
    """Create a minimal fake repo with .git + .scars, write provided scar files."""
    (tmp_path / ".git").mkdir(parents=True, exist_ok=True)
    init_scars(tmp_path)
    for name, text in scars.items():
        (tmp_path / ".scars" / name).write_text(text)
    store = ScarStore.discover(tmp_path)
    assert store is not None
    return store


def _make_repo_context(tracked_paths: list[str], contents: dict[str, str] | None = None):
    """Return a RepoContext-like object for tests (no real git)."""
    from scar.orphan import RepoContext
    return RepoContext(tracked_paths=tracked_paths, file_contents=contents or {})


# ---------------------------------------------------------------------------
# TEST 1: dead path_anchor → scar detected as orphan
# ---------------------------------------------------------------------------

def test_dead_path_anchor_detected(tmp_path):
    store = _make_store(tmp_path, {
        "0001-gone.deadend.md": _scar(id=1, status="active", path_anchors=["src/old_module/"]),
    })
    ctx = _make_repo_context([])  # no tracked files at all
    findings = detect_orphans(store, ctx)
    assert len(findings) == 1
    assert findings[0].scar_id == 1


# ---------------------------------------------------------------------------
# TEST 2: dead pattern_anchor (matches no path, no content) → detected
# ---------------------------------------------------------------------------

def test_dead_pattern_anchor_detected(tmp_path):
    store = _make_store(tmp_path, {
        "0002-old.deadend.md": _scar(id=2, status="active", pattern_anchors=["OldWidget"]),
    })
    ctx = _make_repo_context(
        tracked_paths=["src/main.py"],
        contents={"src/main.py": "class NewWidget: pass"},
    )
    findings = detect_orphans(store, ctx)
    assert len(findings) == 1
    assert findings[0].scar_id == 2


# ---------------------------------------------------------------------------
# TEST 3: one live anchor + one dead anchor → NOT orphan (partial survival)
# ---------------------------------------------------------------------------

def test_partial_survival_not_orphan(tmp_path):
    store = _make_store(tmp_path, {
        "0003-mixed.deadend.md": _scar(
            id=3, status="active",
            path_anchors=["src/live_module/"],   # live
            pattern_anchors=["DeadPattern"],      # dead
        ),
    })
    ctx = _make_repo_context(
        tracked_paths=["src/live_module/foo.py"],
        contents={"src/live_module/foo.py": "class SomeThing: pass"},
    )
    findings = detect_orphans(store, ctx)
    assert findings == []


# ---------------------------------------------------------------------------
# TEST 4: pattern_anchor matches tracked file CONTENT → NOT orphan
# ---------------------------------------------------------------------------

def test_pattern_anchor_content_match_not_orphan(tmp_path):
    store = _make_store(tmp_path, {
        "0004-content.deadend.md": _scar(id=4, status="active", pattern_anchors=["redis"]),
    })
    ctx = _make_repo_context(
        tracked_paths=["services/cache.py"],
        contents={"services/cache.py": "import redis\nclient = redis.Redis()"},
    )
    findings = detect_orphans(store, ctx)
    assert findings == []


# ---------------------------------------------------------------------------
# TEST 5: path_anchor matches existing tracked path → NOT orphan
# ---------------------------------------------------------------------------

def test_path_anchor_live_not_orphan(tmp_path):
    store = _make_store(tmp_path, {
        "0005-live.deadend.md": _scar(id=5, status="active", path_anchors=["payments/"]),
    })
    ctx = _make_repo_context(
        tracked_paths=["payments/retry.py", "payments/models.py"],
    )
    findings = detect_orphans(store, ctx)
    assert findings == []


# ---------------------------------------------------------------------------
# TEST 6: archived, candidate, orphaned scars are NOT scanned
# ---------------------------------------------------------------------------

def test_non_firing_statuses_not_scanned(tmp_path):
    store = _make_store(tmp_path, {
        "0006-arch.deadend.md": _scar(id=6, status="archived", path_anchors=["gone/"]),
        "0007-orphaned.deadend.md": _scar(id=7, status="orphaned", path_anchors=["gone/"]),
        # candidate lives in candidates/ subdir — but we also test status directly
        "0008-challenged.deadend.md": _scar(id=8, status="challenged", path_anchors=["present/"]),
    })
    ctx = _make_repo_context(
        tracked_paths=["present/file.py"],  # 8 would survive; 6+7 would be dead
    )
    findings = detect_orphans(store, ctx)
    # only challenged with dead anchor would fire; 8 has live anchor → nothing
    assert findings == []

    # now kill the anchor for challenged too
    store2 = _make_store(tmp_path / "repo2", {
        "0009-arch.deadend.md": _scar(id=9, status="archived", path_anchors=["gone/"]),
        "0010-orphaned.deadend.md": _scar(id=10, status="orphaned", path_anchors=["gone/"]),
        "0011-active.deadend.md": _scar(id=11, status="active", path_anchors=["also_gone/"]),
        # challenged IS in the firing set — a dead challenged scar MUST be detected.
        "0013-challenged.deadend.md": _scar(id=13, status="challenged", path_anchors=["dead_too/"]),
    })
    ctx2 = _make_repo_context([])
    findings2 = detect_orphans(store2, ctx2)
    # active(11) AND challenged(13) detected; archived(9) and orphaned(10) skipped
    assert sorted(f.scar_id for f in findings2) == [11, 13]


# ---------------------------------------------------------------------------
# TEST 7: invalid regex pattern_anchor → no crash; anchor treated as dead
# ---------------------------------------------------------------------------

def test_invalid_regex_no_crash(tmp_path):
    store = _make_store(tmp_path, {
        "0012-bad-rx.deadend.md": _scar(
            id=12, status="active",
            pattern_anchors=["[invalid_regex("],  # bad regex — treated as dead
        ),
    })
    ctx = _make_repo_context(["src/anything.py"], {"src/anything.py": "x = 1"})
    # Should not raise; scar has no live anchors → detected as orphan
    findings = detect_orphans(store, ctx)
    assert len(findings) == 1
    assert findings[0].scar_id == 12


# ---------------------------------------------------------------------------
# TEST 8: unparseable/malformed scar file → skipped without crashing
# ---------------------------------------------------------------------------

def test_malformed_scar_file_skipped(tmp_path):
    store = _make_store(tmp_path, {
        "0013-bad.deadend.md": "not yaml frontmatter at all",
        "0014-good.deadend.md": _scar(id=14, status="active", path_anchors=["dead_path/"]),
    })
    ctx = _make_repo_context([])
    # 0013 skipped gracefully; 0014 detected
    findings = detect_orphans(store, ctx)
    assert [f.scar_id for f in findings] == [14]


# ---------------------------------------------------------------------------
# TEST 9: OrphanFinding reports which specific anchors failed
# ---------------------------------------------------------------------------

def test_orphan_finding_reports_failed_anchors(tmp_path):
    store = _make_store(tmp_path, {
        "0015-detail.deadend.md": _scar(
            id=15, status="active",
            path_anchors=["dead/path/"],
            pattern_anchors=["NoSuchPattern"],
        ),
    })
    ctx = _make_repo_context([])
    findings = detect_orphans(store, ctx)
    assert len(findings) == 1
    f = findings[0]
    assert f.scar_id == 15
    assert "dead/path/" in f.dead_path_anchors
    assert "NoSuchPattern" in f.dead_pattern_anchors


# ---------------------------------------------------------------------------
# TEST 10: anchors_all_dead helper — orphaned scar whose anchors now live → False
# ---------------------------------------------------------------------------

def test_anchors_all_dead_returns_false_when_anchors_live(tmp_path):
    from scar.model import parse_scar_text
    scar_text = _scar(id=16, status="orphaned", path_anchors=["src/revived/"])
    scar = parse_scar_text(scar_text)
    ctx = _make_repo_context(["src/revived/module.py"])
    assert anchors_all_dead(scar, ctx) is False


def test_anchors_all_dead_returns_true_when_all_dead(tmp_path):
    from scar.model import parse_scar_text
    scar_text = _scar(id=17, status="orphaned", path_anchors=["src/truly_gone/"])
    scar = parse_scar_text(scar_text)
    ctx = _make_repo_context([])
    assert anchors_all_dead(scar, ctx) is True


# ---------------------------------------------------------------------------
# RepoContext builder: reads git ls-files + file excerpts from a real repo
# ---------------------------------------------------------------------------

def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_build_repo_context_lists_tracked_and_reads_content(tmp_path):
    from scar.orphan import build_repo_context
    _git_repo(tmp_path, {"src/app.py": "import redis\n", "README.md": "# hi\n"})
    ctx = build_repo_context(tmp_path)
    assert "src/app.py" in ctx.tracked_paths
    assert "README.md" in ctx.tracked_paths
    assert "redis" in ctx.file_contents["src/app.py"]


def test_build_repo_context_skips_binary_files(tmp_path):
    from scar.orphan import build_repo_context
    _git_repo(tmp_path, {"blob.bin": b"\xff\xfe\x00\x80garbage"})
    ctx = build_repo_context(tmp_path)
    # tracked, but content not loaded (undecodable/binary)
    assert "blob.bin" in ctx.tracked_paths
    assert "blob.bin" not in ctx.file_contents


def test_build_repo_context_skips_oversize_files(tmp_path):
    from scar.orphan import MAX_CONTENT_BYTES, build_repo_context
    big = "x" * (MAX_CONTENT_BYTES + 10)
    _git_repo(tmp_path, {"huge.txt": big})
    ctx = build_repo_context(tmp_path)
    assert "huge.txt" in ctx.tracked_paths
    assert "huge.txt" not in ctx.file_contents
