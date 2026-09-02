"""Anchor matching + injection ranking — the read-side brain."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scar import symbols
from scar.match import (
    MAX_ANCHOR_SCAN,
    _pattern_anchor_matches,
    find_violations,
    find_violations_for_diff,
    has_content_signal,
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


def test_census_counts_every_match_before_top_k_truncates(tmp_path):
    """#286: `count` on a firing row is min(matched, top_k), so co-fires per
    edit have been capped at DEFAULT_TOP_K in the log since the cap existed.
    The census is taken from _match_target BEFORE _select_top, so it can say
    seven when the injected list says three."""
    from scar.match import rank_and_census_for_edit
    store = make_repo(tmp_path)
    for i in range(3, 8):
        (tmp_path / ".scars" / f"000{i}-x{i}.fence.md").write_text(
            FENCE.replace("id: 1", f"id: {i}"))
    matches, census = rank_and_census_for_edit(
        store, tmp_path / "payments" / "x.py", "", top_k=3)
    assert len(matches) == 3
    assert census.total == 6
    assert census.path_only == 6
    assert census.content == 0


def test_census_splits_content_signal_from_path_proximity(tmp_path):
    """The split _select_top computes to tier the fatigue budget is the same
    split a precision reader needs, and it must be counted before the cut:
    one content hit plus one path-proximity match is 2/1/1, whatever top_k
    then keeps."""
    from scar.match import rank_and_census_for_edit
    store = make_repo(tmp_path)
    matches, census = rank_and_census_for_edit(
        store, tmp_path / "payments" / "retry.py", "import redis", top_k=1)
    assert len(matches) == 1
    assert census.total == 2
    assert census.content == 1
    assert census.path_only == 1


def test_census_is_none_when_target_is_outside_the_store(tmp_path):
    """Outside the repo there is nothing to count. None, not zeros: zeros
    would claim an edit was observed and matched nothing."""
    from scar.match import rank_and_census_for_edit
    store = make_repo(tmp_path)
    matches, census = rank_and_census_for_edit(store, Path("/elsewhere/x.py"), "")
    assert matches == []
    assert census is None


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
    renamed = {"path_anchors", "pattern_anchors", "symbol_anchors", "command_anchors"}  # carried under anchors.*
    for f in fields(Scar):
        if f.name in renamed:
            continue
        assert f.name in d, f"Scar.{f.name} missing from ScarMatch.to_dict()"
    assert d["anchors"] == {"paths": ["payments/"], "patterns": [], "symbols": [], "commands": []}


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
    import os
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


def test_content_pattern_is_content_signal():
    assert has_content_signal(SimpleNamespace(matched_by=("path", "content_pattern")))


def test_symbol_is_content_signal():
    assert has_content_signal(SimpleNamespace(matched_by=("symbol",)))


def test_path_only_is_not_content_signal():
    assert not has_content_signal(SimpleNamespace(matched_by=("path", "path_pattern")))


def test_empty_matched_by_is_not_content_signal():
    assert not has_content_signal(SimpleNamespace(matched_by=()))


# ---------------------------------------------------------------------------
# Task 4: single-source violation matcher — find_violations / _for_diff
# ---------------------------------------------------------------------------

def _write_violation_scar(tmp_path, violation=None):
    """One scar path-anchored to payments/, optionally carrying `violation`."""
    (tmp_path / ".git").mkdir(exist_ok=True)
    init_scars(tmp_path)
    lines = [
        "---",
        "id: 9",
        "type: fence",
        "title: Sleep cap",
        "severity: critical",
        "confidence: 0.9",
        "created: 2026-06-09",
        "authors: [mara]",
        "anchors:",
        "  - path: payments/",
        "evidence:",
        "  - commit: ddd4444",
    ]
    if violation is not None:
        lines.append(f'violation: "{violation}"')
    lines += ["status: active", "---", "", "Do not lower the sleep.", ""]
    (tmp_path / ".scars" / "0009-sleep.fence.md").write_text("\n".join(lines))
    return ScarStore.discover(tmp_path)


SLEEP_VIOLATION = "sleep\\((?:[0-6])\\)"


def test_violation_fires_on_anchored_path_with_matching_added_content(tmp_path):
    store = _write_violation_scar(tmp_path, SLEEP_VIOLATION)
    hits = find_violations(store, "payments/x.py", "time.sleep(3)")
    assert len(hits) == 1
    assert hits[0].path == "payments/x.py"
    assert "time.sleep(3)" in hits[0].excerpt
    assert hits[0].scar.id == 9


def test_violation_gated_by_path_anchor_proximity(tmp_path):
    store = _write_violation_scar(tmp_path, SLEEP_VIOLATION)
    # same offending content, but the file isn't under the scar's path anchor
    hits = find_violations(store, "docs/x.md", "time.sleep(3)")
    assert hits == []


def test_scar_without_violation_field_never_returns(tmp_path):
    store = _write_violation_scar(tmp_path, violation=None)
    hits = find_violations(store, "payments/x.py", "time.sleep(3)")
    assert hits == []


def test_invalid_violation_regex_is_silently_ignored(tmp_path):
    store = _write_violation_scar(tmp_path, "sleep((")  # unbalanced -> re.error
    hits = find_violations(store, "payments/x.py", "time.sleep(3)")
    assert hits == []


def test_diff_violation_matches_added_lines(tmp_path):
    store = _write_violation_scar(tmp_path, SLEEP_VIOLATION)
    diff = """\
diff --git a/payments/x.py b/payments/x.py
--- a/payments/x.py
+++ b/payments/x.py
@@ -0,0 +1 @@
+time.sleep(2)
"""
    hits = find_violations_for_diff(store, diff)
    assert len(hits) == 1
    assert hits[0].path == "payments/x.py"
    assert "time.sleep(2)" in hits[0].excerpt


def test_diff_violation_ignores_context_lines(tmp_path):
    store = _write_violation_scar(tmp_path, SLEEP_VIOLATION)
    # the offending call only appears as an unchanged context line; the added
    # line ("sleep(9)") is out of the matched range and must not fire either
    diff = """\
diff --git a/payments/y.py b/payments/y.py
--- a/payments/y.py
+++ b/payments/y.py
@@ -1,3 +1,3 @@
 def f():
-    time.sleep(1)
+    time.sleep(9)
     time.sleep(2)
"""
    hits = find_violations_for_diff(store, diff)
    assert hits == []


def test_violation_regex_matches_across_line_boundaries(tmp_path):
    # Violation regex spanning line boundary (foo\s+bar matching "foo\nbar")
    # must match against the whole capped text, not per-line.
    store = _write_violation_scar(tmp_path, "foo\\s+bar")
    hits = find_violations(store, "payments/test.py", "foo\nbar\n")
    assert len(hits) == 1
    assert hits[0].excerpt == "foo"


def _write_scars_anchored_scar(tmp_path, scar_id, slug):
    """One scar path-anchored to .scars/ itself, carrying SLEEP_VIOLATION.
    Its body quotes the forbidden construct, as any well-written scar must."""
    (tmp_path / ".git").mkdir(exist_ok=True)
    init_scars(tmp_path)
    lines = [
        "---",
        f"id: {scar_id}",
        "type: landmine",
        f"title: Sleep cap {scar_id}",
        "severity: high",
        "confidence: 0.9",
        "created: 2026-07-03",
        "authors: [mara]",
        "anchors:",
        "  - path: .scars/",
        f'violation: "{SLEEP_VIOLATION}"',
        "evidence:",
        "  - issue: 148",
        "status: active",
        "---",
        "",
        "Never write time.sleep(3) in payments code.",
        "",
    ]
    rel = f".scars/{slug}"
    (tmp_path / rel).write_text("\n".join(lines))
    return ScarStore.discover(tmp_path), rel


def test_violation_never_fires_on_scars_own_file(tmp_path):
    # Editing a scar's own file must not trip that scar's violation regex —
    # the body quotes the forbidden construct by design (issue #148).
    store, rel = _write_scars_anchored_scar(tmp_path, 21, "0021-own.landmine.md")
    hits = find_violations(store, rel, "avoid time.sleep(3) here")
    assert hits == []


def test_violation_still_fires_on_other_scar_file(tmp_path):
    # Self-exclusion only: a .scars/-anchored violation (scar #5's design)
    # must still fire when a DIFFERENT scar file gains offending content.
    store, _ = _write_scars_anchored_scar(tmp_path, 21, "0021-own.landmine.md")
    hits = find_violations(store, ".scars/0022-other.landmine.md",
                           "evidence quoting time.sleep(3)")
    assert len(hits) == 1
    assert hits[0].scar.id == 21


def test_diff_violation_never_fires_on_scars_own_file(tmp_path):
    store, rel = _write_scars_anchored_scar(tmp_path, 21, "0021-own.landmine.md")
    diff = f"""\
diff --git a/{rel} b/{rel}
--- a/{rel}
+++ b/{rel}
@@ -0,0 +1 @@
+avoid time.sleep(3) here
"""
    hits = find_violations_for_diff(store, diff)
    assert hits == []


# --- command anchors (#175) ---

COMMAND_SCAR = """\
---
id: 7
type: deadend
title: Bare uv sync strips extras
severity: high
confidence: 0.9
created: 2026-07-30
authors: ["kib"]
anchors:
  - command: "uv sync(?!.* --all-extras)"
evidence:
  - issue: 175
status: active
---

Always run uv sync --all-extras.
"""


def _command_repo(tmp_path):
    store = make_repo(tmp_path)
    (tmp_path / ".scars" / "0007-uv-sync.deadend.md").write_text(COMMAND_SCAR)
    return store


def test_rank_matches_for_command_fires_on_matching_command(tmp_path):
    from scar.match import rank_matches_for_command
    store = _command_repo(tmp_path)
    hits = rank_matches_for_command(store, "uv sync")
    assert [m.scar.id for m in hits] == [7]
    assert hits[0].matched_by == ("command",)
    assert has_content_signal(hits[0])   # command hit renders full body


def test_rank_matches_for_command_silent_on_innocent_command(tmp_path):
    from scar.match import rank_matches_for_command
    store = _command_repo(tmp_path)
    assert rank_matches_for_command(store, "uv sync --all-extras") == []
    assert rank_matches_for_command(store, "git status") == []


def test_command_census_counts_before_top_k_and_is_all_content(tmp_path):
    """#286 on the command path. A command hit IS the mistake, so every match
    is content-signal by construction and path_only is always 0. The count
    still has to be taken before top_k or a repo with four overlapping
    command scars logs three forever."""
    from scar.match import rank_and_census_for_command
    store = _command_repo(tmp_path)
    for i in (8, 9):
        (tmp_path / ".scars" / f"000{i}-uv{i}.deadend.md").write_text(
            COMMAND_SCAR.replace("id: 7", f"id: {i}"))
    matches, census = rank_and_census_for_command(store, "uv sync", top_k=2)
    assert len(matches) == 2
    assert census.total == 3
    assert census.content == 3
    assert census.path_only == 0


def test_targets_census_is_per_target_and_pre_merge(tmp_path):
    """#286 on the multi-file path. Codex logs one row per file, so the census
    is keyed by relative path and taken before the per-target cut AND before
    merge_best_matches dedups across files. A file outside the store gets no
    entry rather than zeros."""
    from scar.match import rank_and_census_for_targets
    store = make_repo(tmp_path)
    for i in range(3, 6):
        (tmp_path / ".scars" / f"000{i}-x{i}.fence.md").write_text(
            FENCE.replace("id: 1", f"id: {i}"))
    targets = [(tmp_path / "payments" / "a.py", "import redis"),
               (tmp_path / "brand" / "b.py", "nothing"),
               (Path("/elsewhere/c.py"), "import redis")]
    matches, census = rank_and_census_for_targets(store, targets, top_k=2)
    assert len(matches) == 2
    assert set(census) == {"payments/a.py", "brand/b.py"}
    assert census["payments/a.py"].to_dict() == {"total": 5, "content": 1, "path_only": 4}
    assert census["brand/b.py"].to_dict() == {"total": 0, "content": 0, "path_only": 0}


def test_edit_matching_never_fires_command_anchors(tmp_path):
    store = _command_repo(tmp_path)
    hits = rank_matches_for_edit(store, tmp_path / "uv sync", "uv sync")
    assert all(m.scar.id != 7 for m in hits)


# --- tiering before truncation (#185) ---

def _breadth_repo(tmp_path):
    """Three broad critical path-only scars that outrank one low-severity
    content-signal scar — the #185 truncation shape."""
    store = make_repo(tmp_path)
    for i, name in enumerate(("alpha", "beta", "gamma"), start=2):
        (tmp_path / ".scars" / f"000{i}-{name}.fence.md").write_text(f"""---
id: {i}
type: fence
title: broad {name}
severity: critical
confidence: 1.0
created: 2026-08-01
authors: ["kib"]
anchors:
  - path: payments/
evidence:
  - issue: 185
status: active
---

broad {name} body
""")
    (tmp_path / ".scars" / "0005-narrow.deadend.md").write_text("""---
id: 5
type: deadend
title: narrow content trap
severity: low
confidence: 0.3
created: 2026-08-01
authors: ["kib"]
anchors:
  - path: payments/
  - pattern: "forbidden_widget"
evidence:
  - issue: 185
status: active
---

do not use forbidden_widget
""")
    return store


def test_content_signal_match_survives_topk_over_louder_path_matches(tmp_path):
    store = _breadth_repo(tmp_path)
    hits = rank_matches_for_edit(store, tmp_path / "payments" / "x.py",
                                 "y = forbidden_widget()")
    ids = [m.scar.id for m in hits]
    assert 5 in ids, f"content-signal scar deleted by truncation: {ids}"
    assert len(hits) <= 3


def test_path_only_matches_still_fill_remaining_slots(tmp_path):
    store = _breadth_repo(tmp_path)
    hits = rank_matches_for_edit(store, tmp_path / "payments" / "x.py",
                                 "y = forbidden_widget()")
    assert sum(1 for m in hits if not has_content_signal(m)) == 2


def test_no_content_signal_keeps_pure_rank_order(tmp_path):
    store = _breadth_repo(tmp_path)
    hits = rank_matches_for_edit(store, tmp_path / "payments" / "x.py", "")
    assert [m.scar.id for m in hits] == sorted(
        (m.scar.id for m in hits),
        key=lambda i: next(-m.rank for m in hits if m.scar.id == i))
    assert 5 not in [m.scar.id for m in hits][:3] or len(hits) == 3
