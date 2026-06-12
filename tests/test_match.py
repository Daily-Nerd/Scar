"""Anchor matching + injection ranking — the read-side brain."""

from pathlib import Path

from scar.match import rank_for_edit, rank_matches_for_diff, rank_matches_for_edit
from scar.store import ScarStore, init_scars

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
