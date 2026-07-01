"""Anchor matching + injection ranking — the read-side brain."""

from pathlib import Path

import pytest

from scar import symbols
from scar.match import (
    MAX_ANCHOR_SCAN,
    _pattern_anchor_matches,
    rank_for_edit,
    rank_matches_for_diff,
    rank_matches_for_edit,
)
from scar.store import ScarStore, init_scars

symbols_extra = pytest.mark.skipif(
    not symbols.symbols_available(), reason="tree-sitter extra not installed")

FENCE = """\
---
id: 1
type: fence
title: Sleep is 7s for vendor window
severity: critical
confidence: 0.9
created: 2026-06-09
authors: [mara]
anchors:
  - path: payments/
evidence:
  - commit: aaa1111
status: active
---

Do not lower the sleep.
"""

DEADEND = """\
---
id: 2
type: deadend
title: No redis for sessions
severity: medium
confidence: 0.8
created: 2026-06-09
authors: [mara]
anchors:
  - pattern: "redis"
evidence:
  - commit: bbb2222
status: active
---

Redis eviction lost sessions.
"""


def make_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-vendor.fence.md").write_text(FENCE)
    (tmp_path / ".scars" / "0002-redis.deadend.md").write_text(DEADEND)
    return ScarStore.discover(tmp_path)


def test_anchor_scan_is_capped():
    # The cap is applied before search(): text longer than MAX_ANCHOR_SCAN is
    # truncated, so a pattern that only matches beyond the cap does not fire.
    beyond_cap = "x" * MAX_ANCHOR_SCAN + "needle"
    assert _pattern_anchor_matches("needle", beyond_cap) is False
    assert _pattern_anchor_matches("needle", "needle" + "x" * MAX_ANCHOR_SCAN) is True


def test_path_anchor_matches_file_under_dir(tmp_path):
    store = make_repo(tmp_path)
    hits = rank_for_edit(store, tmp_path / "payments" / "retry.py", "")
    assert [s.id for s in hits] == [1]


def test_pattern_anchor_fires_on_new_content_in_any_file(tmp_path):
    store = make_repo(tmp_path)
    hits = rank_for_edit(store, tmp_path / "brand" / "new.py", "import redis")
    assert [s.id for s in hits] == [2]


def test_no_match_returns_empty(tmp_path):
    store = make_repo(tmp_path)
    assert rank_for_edit(store, tmp_path / "docs" / "x.md", "nothing") == []


def test_content_match_outranks_path_match_at_equal_severity(tmp_path):
    store = make_repo(tmp_path)
    # make severities equal so anchor strength decides
    f = tmp_path / ".scars" / "0001-vendor.fence.md"
    f.write_text(FENCE.replace("severity: critical", "severity: medium"))
    hits = rank_for_edit(store, tmp_path / "payments" / "retry.py", "redis cache")
    assert [s.id for s in hits] == [2, 1]


def test_top_k_cap(tmp_path):
    store = make_repo(tmp_path)
    for i in range(3, 8):
        (tmp_path / ".scars" / f"000{i}-x{i}.fence.md").write_text(
            FENCE.replace("id: 1", f"id: {i}"))
    hits = rank_for_edit(store, tmp_path / "payments" / "x.py", "", top_k=3)
    assert len(hits) == 3


def test_archived_scars_never_fire(tmp_path):
    store = make_repo(tmp_path)
    f = tmp_path / ".scars" / "0001-vendor.fence.md"
    f.write_text(FENCE.replace("status: active", "status: archived"))
    assert rank_for_edit(store, tmp_path / "payments" / "retry.py", "") == []


def test_structured_match_explains_source_and_signal(tmp_path):
    store = make_repo(tmp_path)
    hits = rank_matches_for_edit(store, tmp_path / "brand" / "new.py", "import redis")
    assert hits[0].source.as_posix() == ".scars/0002-redis.deadend.md"
    assert hits[0].matched_by == ("content_pattern",)
    assert hits[0].to_dict()["anchors"]["patterns"] == ["redis"]


def test_diff_matching_uses_added_lines(tmp_path):
    store = make_repo(tmp_path)
    diff = """\
diff --git a/services/session.py b/services/session.py
--- a/services/session.py
+++ b/services/session.py
@@ -0,0 +1,2 @@
+import redis
+client = redis.Redis()
"""
    hits = rank_matches_for_diff(store, diff)
    assert [m.scar.id for m in hits] == [2]


def test_symbol_anchors_exposed_in_to_dict(tmp_path):
    from scar.model import Scar
    from scar.match import ScarMatch
    from pathlib import Path
    s = Scar(title="t", path_anchors=["a.py"], symbol_anchors=["Foo"], status="active")
    m = ScarMatch(scar=s, source=Path("x.md"), rank=1.0, anchor_strength=2.0,
                  matched_by=("path",), path="a.py")
    assert m.to_dict()["anchors"]["symbols"] == ["Foo"]


def test_match_to_dict_carries_every_scar_field(tmp_path):
    """A new Scar field must never silently vanish from MCP responses."""
    from dataclasses import fields
    from scar.model import Scar
    store = make_repo(tmp_path)
    d = rank_matches_for_edit(store, tmp_path / "payments" / "x.py", "")[0].to_dict()
    renamed = {"path_anchors", "pattern_anchors", "symbol_anchors"}  # carried under anchors.*
    for f in fields(Scar):
        if f.name in renamed:
            continue
        assert f.name in d, f"Scar.{f.name} missing from ScarMatch.to_dict()"
    assert d["anchors"] == {"paths": ["payments/"], "patterns": [], "symbols": []}


def test_diff_ranking_parses_store_once(tmp_path, monkeypatch):
    """Inject hot path: one store walk per diff, not one per diff file."""
    store = make_repo(tmp_path)
    calls = {"n": 0}
    original = ScarStore.parsed

    def counting(self):
        calls["n"] += 1
        return original(self)

    monkeypatch.setattr(ScarStore, "parsed", counting)
    diff = """\
diff --git a/payments/a.py b/payments/a.py
--- a/payments/a.py
+++ b/payments/a.py
@@ -0,0 +1 @@
+x = 1
diff --git a/payments/b.py b/payments/b.py
--- a/payments/b.py
+++ b/payments/b.py
@@ -0,0 +1 @@
+import redis
"""
    matches = rank_matches_for_diff(store, diff)
    assert matches and calls["n"] == 1


@symbols_extra
def test_symbol_anchor_fires_and_outranks_bare_path(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "store.py").write_text(
        "class SessionStore:\n    def save(self):\n        return 1\n")
    scars = tmp_path / ".scars"
    scars.mkdir()
    (scars / "s.md").write_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 1.0\n"
        "anchors:\n  - path: src\n  - symbol: SessionStore\nstatus: active\n---\nbody\n")
    store = ScarStore.discover(tmp_path)
    matches = rank_matches_for_edit(store, tmp_path / "src" / "store.py", "")
    assert matches
    assert "symbol" in matches[0].matched_by
    assert matches[0].anchor_strength == 2.25


@symbols_extra
def test_symbol_match_reflects_file_edits_within_process(tmp_path):
    # Guards against the module-level _read_source cache serving stale content
    # in a long-lived process (MCP server). After rewriting the file so the
    # symbol no longer exists, the match must drop.
    from scar.store import ScarStore
    from scar.match import rank_matches_for_edit
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "store.py"
    f.write_text("class SessionStore:\n    def save(self):\n        return 1\n")
    scars = tmp_path / ".scars"
    scars.mkdir()
    (scars / "s.md").write_text(
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 1.0\n"
        "anchors:\n  - symbol: SessionStore\nstatus: active\n---\nbody\n")
    store = ScarStore.discover(tmp_path)
    assert rank_matches_for_edit(store, f, "")  # matches while symbol present
    # rewrite so the symbol is gone; mtime advances
    import os, time
    later = os.stat(f).st_mtime_ns + 1_000_000
    f.write_text("def unrelated():\n    return 0\n")
    os.utime(f, ns=(later, later))
    assert not rank_matches_for_edit(store, f, "")  # stale cache would still match


# ---------------------------------------------------------------------------
# Issue #109 / #111: rename-following AND re-anchoring are explicitly OFF the
# hot path. match.py (this module) does the anchor scoring on every
# edit/diff; hooks.py wraps it for the precheck hook. Neither may invoke git
# — rename-following (scar.renames, used by scar.orphan) and re-anchor
# tracing (scar.reanchor) must stay confined to the explicit read commands.
# Source-scan, not import-graph, so this fails loudly even if a transitive
# import chain sneaks git access in via an innocuous-looking helper import.
# ---------------------------------------------------------------------------

def test_hot_path_modules_never_invoke_git():
    import scar.hooks as hooks_mod
    import scar.match as match_mod

    for mod in (match_mod, hooks_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "import subprocess" not in src, f"{mod.__name__} must never invoke git"
        assert "subprocess.run" not in src
        assert "subprocess.Popen" not in src
        for banned in (".orphan", ".renames", ".evidence", ".reanchor"):
            assert banned not in src, f"{mod.__name__} imports {banned} (git-touching module)"
