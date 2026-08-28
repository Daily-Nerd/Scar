"""Orphan detection — RED→GREEN per test.

Fixture pattern mirrors test_match.py: tmp-repo with .git/ + .scars/,
scars written directly as YAML.  git ls-files is simulated by giving the
detector an explicit set of tracked paths via a RepoContext object.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scar import symbols
from scar.store import ScarStore, init_scars
from scar.orphan import anchors_all_dead, detect_orphans

symbols_extra = pytest.mark.skipif(
    not symbols.symbols_available(), reason="tree-sitter extra not installed")

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


# ---------------------------------------------------------------------------
# PARTIAL-ROT (#35): a firing scar with ≥1 dead AND ≥1 live anchor.
# Distinct from orphan detection (which fires only when EVERY anchor is dead).
# ---------------------------------------------------------------------------

def test_partial_rot_mixed_live_and_dead_reported(tmp_path):
    """A firing scar with one live and one dead anchor → partial-rot finding
    naming exactly the dead anchor(s). NOT an orphan."""
    from scar.orphan import detect_partial_rot
    store = _make_store(tmp_path, {
        "0001-mix.deadend.md": _scar(
            id=1, status="active",
            path_anchors=["src/live/", "hook/gone.py"],  # first live, second dead
        ),
    })
    ctx = _make_repo_context(tracked_paths=["src/live/foo.py"])
    rot = detect_partial_rot(store, ctx)
    assert len(rot) == 1
    assert rot[0].scar_id == 1
    assert rot[0].dead_path_anchors == ["hook/gone.py"]
    assert rot[0].dead_pattern_anchors == []
    # and it must NOT show up as an orphan
    assert detect_orphans(store, ctx) == []


def test_fully_live_scar_has_no_partial_rot(tmp_path):
    from scar.orphan import detect_partial_rot
    store = _make_store(tmp_path, {
        "0002-live.deadend.md": _scar(id=2, status="active",
                                      path_anchors=["src/a/", "src/b/"]),
    })
    ctx = _make_repo_context(tracked_paths=["src/a/x.py", "src/b/y.py"])
    assert detect_partial_rot(store, ctx) == []


def test_fully_dead_scar_is_orphan_not_partial_rot(tmp_path):
    """Every anchor dead → orphan, and explicitly NOT partial-rot."""
    from scar.orphan import detect_partial_rot
    store = _make_store(tmp_path, {
        "0003-dead.deadend.md": _scar(id=3, status="active",
                                      path_anchors=["gone/a/", "gone/b/"]),
    })
    ctx = _make_repo_context(tracked_paths=[])
    assert detect_partial_rot(store, ctx) == []
    assert len(detect_orphans(store, ctx)) == 1


def test_partial_rot_only_scans_firing_statuses(tmp_path):
    from scar.orphan import detect_partial_rot
    store = _make_store(tmp_path, {
        "0004-arch.deadend.md": _scar(id=4, status="archived",
                                      path_anchors=["src/live/", "gone/"]),
        "0005-chal.deadend.md": _scar(id=5, status="challenged",
                                      path_anchors=["src/live/", "gone/"]),
    })
    ctx = _make_repo_context(tracked_paths=["src/live/foo.py"])
    rot = detect_partial_rot(store, ctx)
    # archived not scanned; challenged is firing → reported
    assert [r.scar_id for r in rot] == [5]


# ---------------------------------------------------------------------------
# SELF-REFERENTIAL LIVENESS (#35): a scar must not keep itself alive by
# quoting, in its own .scars/ body, the very pattern it warns about.
# ---------------------------------------------------------------------------

def test_pattern_anchor_matching_only_own_file_is_orphan(tmp_path):
    """A pattern anchor whose ONLY content match is the scar's own .scars file
    → that match is excluded → scar is orphan-detected (all anchors dead)."""
    store = _make_store(tmp_path, {
        "0006-selfref.deadend.md": _scar(id=6, status="active",
                                         pattern_anchors=["SelfOnlyToken"]),
    })
    # The scar's own file is the only tracked content carrying the token.
    self_rel = ".scars/0006-selfref.deadend.md"
    ctx = _make_repo_context(
        tracked_paths=[self_rel, "src/other.py"],
        contents={self_rel: "SelfOnlyToken appears here",
                  "src/other.py": "nothing relevant"},
    )
    findings = detect_orphans(store, ctx)
    assert len(findings) == 1
    assert findings[0].scar_id == 6


def test_pattern_anchor_matching_real_code_still_live_despite_own_file(tmp_path):
    """Self-exclusion must not over-fire: if the pattern also matches REAL
    tracked code, the scar stays alive (not orphan)."""
    store = _make_store(tmp_path, {
        "0007-real.deadend.md": _scar(id=7, status="active",
                                      pattern_anchors=["SharedToken"]),
    })
    self_rel = ".scars/0007-real.deadend.md"
    ctx = _make_repo_context(
        tracked_paths=[self_rel, "src/real.py"],
        contents={self_rel: "SharedToken in prose",
                  "src/real.py": "x = SharedToken()"},
    )
    assert detect_orphans(store, ctx) == []


# ---------------------------------------------------------------------------
# Issue #91.2: build_repo_context must surface a git failure, not return an
# empty tracked set (which makes EVERY scar look orphaned -> false CI gate).
# ---------------------------------------------------------------------------

def test_build_repo_context_non_git_dir_surfaces_error(tmp_path):
    from scar.orphan import GitError, build_repo_context
    # tmp_path is not a git repo: git ls-files fails (rc 128). It must NOT be
    # mistaken for "a repo with zero tracked files".
    with pytest.raises(GitError):
        build_repo_context(tmp_path)


# ---------------------------------------------------------------------------
# Issue #99 Phase 3: symbol-drift detection — a symbol anchor that still
# resolves by name but whose body shape changed since the scar's evidence
# commit. Advisory only; never a status transition.
# ---------------------------------------------------------------------------

@symbols_extra
def test_detect_symbol_drift_flags_changed_body(tmp_path):
    import subprocess
    from scar.store import ScarStore
    from scar.orphan import detect_symbol_drift

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    src = tmp_path / "store.py"
    src.write_text("class SessionStore:\n    def save(self):\n        x = 1\n        return x\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                         capture_output=True, text=True, check=True).stdout.strip()

    scars = tmp_path / ".scars"
    scars.mkdir()
    (scars / "s.md").write_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 1.0\n"
        "anchors:\n  - path: store.py\n  - symbol: SessionStore.save\n"
        f"evidence:\n  - commit: {sha}\nstatus: active\n---\nbody\n")
    git("add", "-A")
    git("commit", "-q", "-m", "add scar")
    src.write_text("class SessionStore:\n    def save(self):\n        return compute(other())\n")
    git("add", "-A")
    git("commit", "-q", "-m", "rewrite save")

    store = ScarStore.discover(tmp_path)
    findings = detect_symbol_drift(store, tmp_path)
    assert len(findings) == 1
    assert findings[0].symbol == "SessionStore.save"
    assert findings[0].scar_id is None or isinstance(findings[0].scar_id, int)
    assert 0.0 <= findings[0].similarity < 1.0


@symbols_extra
def test_detect_symbol_drift_ignores_comment_only_change(tmp_path):
    import subprocess
    from scar.store import ScarStore
    from scar.orphan import detect_symbol_drift

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    src = tmp_path / "store.py"
    src.write_text("class SessionStore:\n    def save(self):\n        x = 1\n        return x\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                         capture_output=True, text=True, check=True).stdout.strip()

    scars = tmp_path / ".scars"
    scars.mkdir()
    (scars / "s.md").write_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 1.0\n"
        "anchors:\n  - path: store.py\n  - symbol: SessionStore.save\n"
        f"evidence:\n  - commit: {sha}\nstatus: active\n---\nbody\n")
    git("add", "-A")
    git("commit", "-q", "-m", "add scar")
    # comment-only change at HEAD — the scar has a path anchor too, so the
    # bare symbol resolves against store.py and the fingerprint comparison
    # actually runs (without the path anchor, _drift_path can't resolve a
    # file and the anchor is skipped, making the assertion trivially true).
    src.write_text("class SessionStore:\n    def save(self):\n        # tweak\n        x = 1\n        return x\n")
    git("add", "-A")
    git("commit", "-q", "-m", "comment only")

    store = ScarStore.discover(tmp_path)
    findings = detect_symbol_drift(store, tmp_path)
    assert findings == []  # comment-only → identical fingerprint → no drift


# ---------------------------------------------------------------------------
# Issue #109: rename-following for dead path anchors. detect_orphans and
# detect_partial_rot accept an optional `repo` — when given, a dead CONCRETE
# path anchor that git can resolve to an unambiguous, currently-tracked
# rename target is reported via finding.renamed. Classification (orphan vs
# partial-rot vs live) is unchanged; this only enriches the REPORT.
# ---------------------------------------------------------------------------

def _real_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def _real_commit(tmp_path: Path, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=tmp_path, check=True)


def test_detect_orphans_reports_rename_target(tmp_path):
    from scar.orphan import build_repo_context

    _real_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "old.py").write_text("x = 1\n")
    _real_commit(tmp_path, "add old.py")
    subprocess.run(["git", "mv", "src/old.py", "src/new.py"],
                   cwd=tmp_path, check=True)
    _real_commit(tmp_path, "rename old to new")

    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-x.deadend.md").write_text(
        _scar(id=1, status="active", path_anchors=["src/old.py"]))
    _real_commit(tmp_path, "add scar")

    store = ScarStore.discover(tmp_path)
    ctx = build_repo_context(tmp_path)
    findings = detect_orphans(store, ctx, repo=tmp_path)
    assert len(findings) == 1
    assert findings[0].renamed == {"src/old.py": "src/new.py"}


def test_detect_orphans_without_repo_arg_has_no_renamed(tmp_path):
    """Back-compat: callers that don't pass repo= get no rename enrichment,
    never a crash — existing 2-arg call sites keep working unmodified."""
    store = _make_store(tmp_path, {
        "0001-gone.deadend.md": _scar(id=1, status="active", path_anchors=["src/old_module/"]),
    })
    ctx = _make_repo_context([])
    findings = detect_orphans(store, ctx)
    assert len(findings) == 1
    assert findings[0].renamed == {}


def test_detect_orphans_deleted_not_renamed_has_empty_renamed(tmp_path):
    from scar.orphan import build_repo_context

    _real_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "gone.py").write_text("x = 1\n")
    _real_commit(tmp_path, "add gone.py")
    (tmp_path / "src" / "gone.py").unlink()
    _real_commit(tmp_path, "delete gone.py")

    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-x.deadend.md").write_text(
        _scar(id=1, status="active", path_anchors=["src/gone.py"]))
    _real_commit(tmp_path, "add scar")

    store = ScarStore.discover(tmp_path)
    ctx = build_repo_context(tmp_path)
    findings = detect_orphans(store, ctx, repo=tmp_path)
    assert len(findings) == 1
    assert findings[0].renamed == {}


def test_detect_partial_rot_reports_rename_target(tmp_path):
    from scar.orphan import build_repo_context, detect_partial_rot

    _real_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "live.py").write_text("x = 1\n")
    (tmp_path / "src" / "old.py").write_text("y = 2\n")
    _real_commit(tmp_path, "add files")
    subprocess.run(["git", "mv", "src/old.py", "src/new.py"],
                   cwd=tmp_path, check=True)
    _real_commit(tmp_path, "rename old to new")

    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-x.deadend.md").write_text(
        _scar(id=1, status="active",
              path_anchors=["src/live.py", "src/old.py"]))
    _real_commit(tmp_path, "add scar")

    store = ScarStore.discover(tmp_path)
    ctx = build_repo_context(tmp_path)
    rot = detect_partial_rot(store, ctx, repo=tmp_path)
    assert len(rot) == 1
    assert rot[0].renamed == {"src/old.py": "src/new.py"}
    assert detect_orphans(store, ctx, repo=tmp_path) == []


# ---------------------------------------------------------------------------
# #156 (daimon#72): liveness scan-head truncation — a pattern whose only
# match sits past the old 8KB READ_HEAD_BYTES boundary misreported as dead.
# ---------------------------------------------------------------------------


def test_build_repo_context_reads_beyond_8kb(tmp_path):
    from scar.orphan import build_repo_context
    deep = "x = 0\n" * 1500 + "GROUNDING_MARKER = 1\n"  # marker past byte 8192
    _git_repo(tmp_path, {"deep.py": deep})
    ctx = build_repo_context(tmp_path)
    assert "GROUNDING_MARKER" in ctx.file_contents["deep.py"]


def test_pattern_anchor_live_beyond_old_head(tmp_path):
    from scar.orphan import _pattern_anchor_live, build_repo_context
    deep = "x = 0\n" * 1500 + "GROUNDING_MARKER = 1\n"
    _git_repo(tmp_path, {"deep.py": deep})
    ctx = build_repo_context(tmp_path)
    assert _pattern_anchor_live("GROUNDING_MARKER", ctx)


def test_pattern_liveness_bound_is_max_anchor_scan(tmp_path):
    # The honest residual (#156): content loads in full, but matching runs
    # through the shared primitive's MAX_ANCHOR_SCAN (64 KiB) cap — the ReDoS
    # bound (landmine #11). A match past that horizon stays dead ON PURPOSE;
    # do not "fix" this without replacing the bound.
    from scar.match import MAX_ANCHOR_SCAN
    from scar.orphan import _pattern_anchor_live, build_repo_context
    deep = "x = 0\n" * (MAX_ANCHOR_SCAN // 6 + 200) + "BEYOND_HORIZON = 1\n"
    _git_repo(tmp_path, {"vast.py": deep})
    ctx = build_repo_context(tmp_path)
    assert "BEYOND_HORIZON" in ctx.file_contents["vast.py"]      # loaded...
    assert not _pattern_anchor_live("BEYOND_HORIZON", ctx)       # ...but capped


# --- command anchors (#175): exempt from content liveness ---

def test_command_only_scar_never_orphans(tmp_path):
    scar_text = """---
id: 9
type: deadend
title: command trap
severity: high
confidence: 0.9
created: 2026-07-30
authors: ["kib"]
anchors:
  - command: "uv sync"
evidence:
  - issue: 175
status: active
---

body
"""
    from scar.model import parse_scar_text
    from scar.orphan import anchors_all_dead
    ctx = _make_repo_context(tracked_paths=[])
    scar = parse_scar_text(scar_text)
    assert anchors_all_dead(scar, ctx) is False


# ---------------------------------------------------------------------------
# Alternation branch rot (#213): a live branch must not mask a dead one
# ---------------------------------------------------------------------------

def test_dead_alternation_branch_reported_though_anchor_matches(tmp_path):
    """A pattern anchor 'live|dead' whose second branch matches nothing is
    partial rot even though the anchor as a whole still matches. Without
    per-branch checking the live branch masks the corpse and the gauge reads
    green — the #6 failure mode hidden behind an alternation."""
    from scar.orphan import detect_partial_rot
    store = _make_store(tmp_path, {
        "0001-alt.deadend.md": _scar(
            id=1, status="active",
            path_anchors=["src/live/"],
            pattern_anchors=["shutil|neverappearsanywhere"],
        ),
    })
    ctx = _make_repo_context(
        tracked_paths=["src/live/foo.py"],
        contents={"src/live/foo.py": "import shutil\n"},
    )
    rot = detect_partial_rot(store, ctx)
    assert len(rot) == 1
    assert rot[0].dead_pattern_branches == ["neverappearsanywhere"]


def test_all_alternation_branches_live_is_not_partial_rot(tmp_path):
    """Every branch matching something means nothing rotted — no finding."""
    from scar.orphan import detect_partial_rot
    store = _make_store(tmp_path, {
        "0001-alt.deadend.md": _scar(
            id=1, status="active",
            path_anchors=["src/live/"],
            pattern_anchors=["shutil|pathlib"],
        ),
    })
    ctx = _make_repo_context(
        tracked_paths=["src/live/foo.py"],
        contents={"src/live/foo.py": "import shutil\nimport pathlib\n"},
    )
    assert detect_partial_rot(store, ctx) == []


def test_alternation_inside_a_group_is_not_split(tmp_path):
    """`foo(a|b)` is ONE top-level branch. Splitting inside the group would
    produce phantom branches 'foo(a' and 'b)' and report false rot."""
    from scar.orphan import detect_partial_rot
    store = _make_store(tmp_path, {
        "0001-grp.deadend.md": _scar(
            id=1, status="active",
            path_anchors=["src/live/"],
            pattern_anchors=["shut(il|xx)"],
        ),
    })
    ctx = _make_repo_context(
        tracked_paths=["src/live/foo.py"],
        contents={"src/live/foo.py": "import shutil\n"},
    )
    assert detect_partial_rot(store, ctx) == []


# ---------------------------------------------------------------------------
# revives_if (#205): an archived scar names its resurrection condition
# ---------------------------------------------------------------------------

def _archived_scar(*, id: int, revives_if: str, path_anchors=("src/",)) -> str:
    anchors = "".join(f"  - path: {p}\n" for p in path_anchors)
    return (
        f"---\nid: {id}\ntype: deadend\ntitle: archived {id}\n"
        f"severity: medium\nconfidence: 0.8\ncreated: 2026-08-28\n"
        f'authors: ["t"]\nanchors:\n{anchors}'
        f'evidence:\n  - note: t\nrevives_if: "{revives_if}"\n'
        f"status: archived\n---\n\nBody.\n"
    )


def test_archived_scar_whose_revives_if_matches_is_reported(tmp_path):
    """The hazard's precondition came back — surface it for human
    re-promotion. Never auto-rearm."""
    from scar.orphan import detect_revivals
    store = _make_store(tmp_path, {
        "0001-arch.deadend.md": _archived_scar(id=1, revives_if="args.command"),
    })
    ctx = _make_repo_context(
        tracked_paths=["src/cli.py"],
        contents={"src/cli.py": "dispatch(args.command)\n"},
    )
    found = detect_revivals(store, ctx)
    assert len(found) == 1
    assert found[0].scar_id == 1
    assert found[0].predicate == "args.command"


def test_archived_scar_whose_revives_if_does_not_match_is_silent(tmp_path):
    from scar.orphan import detect_revivals
    store = _make_store(tmp_path, {
        "0001-arch.deadend.md": _archived_scar(id=1, revives_if="neverappears"),
    })
    ctx = _make_repo_context(
        tracked_paths=["src/cli.py"],
        contents={"src/cli.py": "dispatch(args.func)\n"},
    )
    assert detect_revivals(store, ctx) == []


def test_revives_if_does_not_self_match_on_the_scars_own_body(tmp_path):
    """The predicate is written inside the scar's own file, so a naive scan
    matches it against itself and every archived scar reports revived
    forever — the #35 self-reference trap."""
    from scar.orphan import detect_revivals
    store = _make_store(tmp_path, {
        "0001-arch.deadend.md": _archived_scar(id=1, revives_if="neverappears"),
    })
    ctx = _make_repo_context(
        tracked_paths=["src/cli.py", ".scars/0001-arch.deadend.md"],
        contents={
            "src/cli.py": "clean()\n",
            ".scars/0001-arch.deadend.md": (
                tmp_path / ".scars" / "0001-arch.deadend.md").read_text(),
        },
    )
    assert detect_revivals(store, ctx) == []


def test_active_scar_with_revives_if_is_not_a_revival(tmp_path):
    """revives_if only means something for an ARCHIVED scar. A firing scar
    already protects the code; reporting it as revived is noise."""
    from scar.orphan import detect_revivals
    store = _make_store(tmp_path, {
        "0001-live.deadend.md": _archived_scar(
            id=1, revives_if="args.command").replace(
                "status: archived", "status: active"),
    })
    ctx = _make_repo_context(
        tracked_paths=["src/cli.py"],
        contents={"src/cli.py": "dispatch(args.command)\n"},
    )
    assert detect_revivals(store, ctx) == []
