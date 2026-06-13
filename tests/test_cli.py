"""CLI surface: each command's contract, exercised through main()."""

import json
import subprocess

import pytest

from scar.cli import main
from scar.store import ScarStore, init_scars

CANDIDATE = """\
---
type: deadend
title: Tried X, failed
severity: medium
confidence: 0.7
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/
evidence:
  - commit: abc1234
status: candidate
---

Why X failed.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_creates_layout_and_reports(repo, capsys):
    assert main(["init"]) == 0
    assert (repo / ".scars" / "template.md").exists()
    assert ".scars" in capsys.readouterr().out


def test_lint_clean_repo_exits_zero(repo, capsys):
    init_scars(repo)
    assert main(["lint"]) == 0


def test_lint_broken_scar_exits_nonzero_names_file(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-bad.deadend.md").write_text("# nope\n")
    assert main(["lint"]) == 1
    assert "0001-bad.deadend.md" in capsys.readouterr().out


def test_status_counts(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "x.md").write_text(CANDIDATE)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "0 active" in out and "1 candidate" in out


def test_promote_assigns_id_and_reports(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    assert main(["promote", "tried-x", "--reviewer", "kibukx"]) == 0
    assert (repo / ".scars" / "0001-tried-x.deadend.md").exists()


def test_promote_unknown_candidate_fails(repo, capsys):
    init_scars(repo)
    assert main(["promote", "nope"]) == 1


def test_check_lists_scars_for_path(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    (repo / "src").mkdir()
    assert main(["check", "src/thing.py"]) == 0
    assert "Tried X, failed" in capsys.readouterr().out


def test_inject_emits_hook_json(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    capsys.readouterr()  # flush promote's human output before machine-mode JSON
    assert main(["inject", "--path", "src/thing.py", "--content", ""]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "Tried X" in payload["hookSpecificOutput"]["additionalContext"]


def test_inject_diff_with_binary_file_never_crashes(repo, capsys, tmp_path):
    init_scars(repo)
    bad = tmp_path / "binary.diff"
    bad.write_bytes(b"\xff\xfe\x00garbage\x80")
    assert main(["inject", "--diff", str(bad)]) == 0


def test_inject_silent_when_no_match(repo, capsys):
    init_scars(repo)
    assert main(["inject", "--path", "docs/x.md", "--content", ""]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_inject_accepts_unified_diff(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "tried-x.md").write_text(CANDIDATE)
    main(["promote", "tried-x", "--reviewer", "k"])
    capsys.readouterr()
    diff = """\
diff --git a/src/thing.py b/src/thing.py
--- a/src/thing.py
+++ b/src/thing.py
@@ -0,0 +1 @@
+print("x")
"""
    assert main(["inject", "--diff", diff]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "Tried X" in payload["hookSpecificOutput"]["additionalContext"]


def test_agent_config_prints_opencode_mcp_snippet(repo, capsys):
    assert main(["agent", "config", "opencode"]) == 0
    out = capsys.readouterr().out
    assert '"command": ["scar", "mcp"]' in out


def test_agent_doctor_reports_agents_file(repo, capsys):
    (repo / "AGENTS.md").write_text("# rules\n")
    assert main(["agent", "doctor"]) == 0
    assert "AGENTS.md: present" in capsys.readouterr().out


def test_why_on_parent_dir_surfaces_descendant_anchors(repo, capsys):
    """Asking a parent directory for its history must include scars anchored
    deeper inside it — found live: `scar why research` missed a landmine
    anchored at research/experiments/track-a/."""
    init_scars(repo)
    deep = CANDIDATE.replace("  - path: src/", "  - path: src/experiments/track-a/")
    (repo / ".scars" / "candidates" / "deep.md").write_text(deep)
    main(["promote", "deep", "--reviewer", "k"])
    (repo / "src").mkdir()
    capsys.readouterr()
    assert main(["why", "src"]) == 0
    assert "Tried X, failed" in capsys.readouterr().out


def test_no_scars_dir_commands_fail_gracefully(repo, capsys):
    assert main(["status"]) == 1
    assert ".scars" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Orphan surfaces (Issue #33). A firing scar with a dead path anchor and an
# empty (or absent) git index is, by construction, an orphan.
# ---------------------------------------------------------------------------

ORPHAN_SCAR = """\
---
id: 1
type: deadend
title: Anchored to a path that no longer exists
severity: medium
confidence: 0.8
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/long_gone/
evidence:
  - commit: abc1234
status: active
---

The module this anchored to was deleted.
"""

NO_ANCHOR_SCAR = """\
---
id: 2
type: fence
title: Scar that protects nothing
severity: low
confidence: 0.5
created: 2026-06-10
authors: ["claude-code"]
anchors:
evidence:
  - commit: def5678
status: active
---

No anchors at all.
"""


def test_lint_warns_on_detected_orphan_exit_zero(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-gone.deadend.md").write_text(ORPHAN_SCAR)
    assert main(["lint"]) == 0  # warning only, never fails by default
    out = capsys.readouterr().out
    assert "orphan" in out.lower()
    assert "#1" in out
    assert "src/long_gone/" in out  # which anchor is dead


def test_lint_fail_orphans_flag_exits_one(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-gone.deadend.md").write_text(ORPHAN_SCAR)
    assert main(["lint", "--fail-orphans"]) == 1
    assert "orphan" in capsys.readouterr().out.lower()


def test_lint_no_anchor_scar_labeled_distinctly(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0002-empty.fence.md").write_text(NO_ANCHOR_SCAR)
    main(["lint"])
    out = capsys.readouterr().out.lower()
    assert "no anchors" in out  # distinct from "all anchors dead"


PERSISTED_ORPHAN = ORPHAN_SCAR.replace("status: active", "status: orphaned").replace("id: 1", "id: 3")

MULTI_ANCHOR_ORPHAN = """\
---
id: 5
type: deadend
title: Both anchors dead
severity: high
confidence: 0.9
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/dead_dir/
  - pattern: "OldClassName"
evidence:
  - commit: aaa1111
status: active
---

Both the path and the pattern are gone.
"""


def test_orphan_command_lists_failed_anchors_readonly(repo, capsys):
    init_scars(repo)
    f = repo / ".scars" / "0005-both.deadend.md"
    f.write_text(MULTI_ANCHOR_ORPHAN)
    before = f.read_text()
    assert main(["orphan"]) == 0
    out = capsys.readouterr().out
    assert "#5" in out
    assert "src/dead_dir/" in out
    assert "OldClassName" in out
    assert f.read_text() == before  # read-only: file untouched


def test_orphan_apply_persists_status_with_dated_note(repo, capsys, monkeypatch):
    import scar.cli as cli
    monkeypatch.setattr(cli.time, "strftime", lambda fmt: "2026-06-13")
    init_scars(repo)
    f = repo / ".scars" / "0005-both.deadend.md"
    f.write_text(MULTI_ANCHOR_ORPHAN)
    assert main(["orphan", "--apply", "--id", "5", "--reason", "module deleted in #99"]) == 0
    text = f.read_text()
    assert "status: orphaned" in text
    assert "2026-06-13" in text
    assert "module deleted in #99" in text
    assert "src/dead_dir/" in text  # failed anchors recorded in the note


def test_orphan_apply_rejects_unknown_id(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0005-both.deadend.md").write_text(MULTI_ANCHOR_ORPHAN)
    assert main(["orphan", "--apply", "--id", "999", "--reason", "x"]) == 1
    assert "not orphan-detected" in capsys.readouterr().out


DEAD_ANCHOR_CANDIDATE = """\
---
type: deadend
title: Anchored to a vanished path
severity: medium
confidence: 0.7
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/never_existed/
evidence:
  - commit: abc1234
status: candidate
---

Why X failed.
"""


def test_promote_warns_when_anchors_all_dead_but_succeeds(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "candidates" / "dead.md").write_text(DEAD_ANCHOR_CANDIDATE)
    rc = main(["promote", "dead", "--reviewer", "k"])
    out = capsys.readouterr().out
    assert rc == 0  # advisory is NON-blocking
    assert (repo / ".scars" / "0001-dead.deadend.md").exists()
    assert "anchor" in out.lower() and "advisory" in out.lower()


def test_lint_reverse_hint_fires_when_orphaned_anchors_return(tmp_path, monkeypatch, capsys):
    """A persisted-orphaned scar whose anchor path is tracked again should
    surface a 're-activate' hint."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    # anchor path now exists and is tracked → anchors live again
    (tmp_path / "src" / "revived").mkdir(parents=True)
    (tmp_path / "src" / "revived" / "mod.py").write_text("x = 1\n")
    revived = ORPHAN_SCAR.replace("status: active", "status: orphaned") \
        .replace("src/long_gone/", "src/revived/")
    (tmp_path / ".scars" / "0001-back.deadend.md").write_text(revived)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    assert main(["lint"]) == 0
    out = capsys.readouterr().out.lower()
    assert "live again" in out and "#1" in out


# ---------------------------------------------------------------------------
# Evidence-reachability surface (Issue #43, scar #5's expiry condition).
# A commit-SHA receipt that doesn't resolve from HEAD is advisory: warned,
# counted, never gated. Needs a real git repo (reachability is a git fact).
# ---------------------------------------------------------------------------

EVIDENCE_SCAR = """\
---
id: 1
type: deadend
title: Cites a commit that no longer resolves
severity: medium
confidence: 0.8
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/
evidence:
  - commit: deadbeef
status: active
---

The cited SHA was orphaned by a history rewrite.
"""


def test_lint_warns_on_unreachable_evidence_sha(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n")
    (tmp_path / ".scars" / "0001-stale.deadend.md").write_text(EVIDENCE_SCAR)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    assert main(["lint"]) == 0  # advisory, never fails by default
    out = capsys.readouterr().out
    assert "evidence-unreachable" in out.lower()
    assert "#1" in out
    assert "deadbeef" in out
    assert "1 unreachable-evidence" in out  # counted in the summary


def test_status_reports_detected_and_persisted_orphan_counts(repo, capsys):
    init_scars(repo)
    (repo / ".scars" / "0001-gone.deadend.md").write_text(ORPHAN_SCAR)        # detected
    (repo / ".scars" / "0003-already.deadend.md").write_text(PERSISTED_ORPHAN)  # persisted
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "1 orphan-detected" in out
    assert "1 orphaned" in out  # persisted, separately counted


# ---------------------------------------------------------------------------
# Partial-rot surfaces (Issue #35). A firing scar with a mix of live and dead
# anchors is advisory — named, but never an orphan and never a blocking gate.
# Needs a REAL git repo so a live anchor has a tracked path to resolve against.
# ---------------------------------------------------------------------------

PARTIAL_ROT_SCAR = """\
---
id: 7
type: landmine
title: One anchor alive, one rotted
severity: medium
confidence: 0.8
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/live/
  - path: src/gone/
evidence:
  - commit: abc1234
status: active
---

Protects two things; one of them was deleted.
"""


def _git_repo_with_partial_rot(tmp_path):
    """Real git repo: src/live/ is tracked (anchor lives), src/gone/ is not
    (anchor dead) → the scar partially rots."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    init_scars(tmp_path)
    (tmp_path / "src" / "live").mkdir(parents=True)
    (tmp_path / "src" / "live" / "mod.py").write_text("x = 1\n")
    (tmp_path / ".scars" / "0007-rot.landmine.md").write_text(PARTIAL_ROT_SCAR)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)


def test_lint_surfaces_partial_rot_naming_dead_anchor(tmp_path, monkeypatch, capsys):
    _git_repo_with_partial_rot(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["lint"]) == 0  # advisory only, never fails by default
    out = capsys.readouterr().out
    assert "#7" in out
    assert "src/gone/" in out          # the dead anchor is named
    assert "src/live/" not in out      # the live anchor is NOT flagged
    assert "partial" in out.lower()    # labeled as partial rot, not orphan
    assert "orphan-detected: scar #7" not in out  # never reported as an orphan


def test_status_reports_partial_rot_count(tmp_path, monkeypatch, capsys):
    _git_repo_with_partial_rot(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "1 partial-rot" in out
    assert "0 orphan-detected" in out  # a partial-rot scar is NOT an orphan


def test_orphan_command_reports_partial_rot_count(tmp_path, monkeypatch, capsys):
    _git_repo_with_partial_rot(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["orphan"]) == 0
    out = capsys.readouterr().out
    assert "no orphan-detected scars" in out  # zero true orphans
    assert "1 partial-rot" in out             # but partial rot surfaced separately
    assert "#7" in out


# ---------------------------------------------------------------------------
# Harvest ranking surfaces + label instrument (Issue #38, batch 2)
# ---------------------------------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def harvest_repo(tmp_path):
    """Synthetic git history yielding harvest candidates across sections:
    a deleted component, a revert-shaped commit, and a flapping value."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work.parent, "init", "-q", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    comp = work / "apps" / "shortlived"
    comp.mkdir(parents=True)
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: add shortlived app")
    (comp / "deploy.yaml").write_text("replicas: 3\n")
    _git(work, "commit", "-qam", "feat: scale up")
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    _git(work, "commit", "-qam", "fix: revert replicas, instance broke")
    (comp / "deploy.yaml").unlink()
    comp.rmdir()
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "chore: remove shortlived app")
    return work


def test_harvest_prints_id_and_score_per_candidate(harvest_repo, capsys):
    """Each candidate line must surface its stable id and score so a human can
    reference it (the id is what `scar harvest --label` consumes)."""
    assert main(["harvest", str(harvest_repo)]) == 0
    out = capsys.readouterr().out
    from scar.harvest import harvest
    result = harvest(harvest_repo)
    # pick any non-empty section's first candidate; its id and score must print
    for section in result.values():
        for c in section:
            assert c["id"] in out, f"id {c['id']} not surfaced in harvest output"
            # score printed to one decimal, e.g. "3.0"
            assert f"{c['score']:.1f}" in out, f"score {c['score']} not surfaced"


def test_harvest_top_k_returns_n_highest_across_sections(harvest_repo, capsys):
    """--top-k N shows exactly the N highest-scoring candidates across ALL
    sections, ranked by raw score (no cross-type normalization)."""
    from scar.harvest import harvest
    result = harvest(harvest_repo)
    all_cands = [c for section in result.values() for c in section]
    expected = sorted(all_cands, key=lambda c: c["score"], reverse=True)[:2]

    assert main(["harvest", str(harvest_repo), "--top-k", "2"]) == 0
    out = capsys.readouterr().out
    # exactly the 2 highest ids appear; the rest do not
    for c in expected:
        assert c["id"] in out, f"top-k missed expected id {c['id']}"
    excluded = [c for c in all_cands if c not in expected]
    for c in excluded:
        assert c["id"] not in out, f"top-k leaked excluded id {c['id']}"
    # ranked descending: first expected id appears before the second
    assert out.index(expected[0]["id"]) < out.index(expected[1]["id"])


def _first_candidate_id(repo):
    from scar.harvest import harvest
    result = harvest(repo)
    for section in result.values():
        for c in section:
            return c["id"]
    raise AssertionError("fixture produced no candidates")


def test_harvest_label_appends_jsonl_line(harvest_repo, tmp_path, capsys, monkeypatch):
    """--label <id> keep appends one well-formed JSONL line with all fields."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    monkeypatch.setattr(cli.time, "strftime", lambda fmt: "2026-06-13")
    cid = _first_candidate_id(harvest_repo)

    assert main(["harvest", str(harvest_repo), "--label", cid, "keep",
                 "--note", "real deadend"]) == 0
    lines = labels.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["id"] == cid
    assert rec["label"] == "keep"
    assert rec["note"] == "real deadend"
    assert rec["date"] == "2026-06-13"
    assert "repo" in rec


def test_harvest_label_creates_parent_dir(harvest_repo, tmp_path, monkeypatch):
    """The labels dir/file is created on first write if missing."""
    import scar.cli as cli
    labels = tmp_path / "nested" / "experiments" / "harvest" / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    assert not labels.parent.exists()
    assert main(["harvest", str(harvest_repo), "--label", cid, "discard"]) == 0
    assert labels.exists()


def test_harvest_label_appends_not_overwrites(harvest_repo, tmp_path, monkeypatch):
    """A second --label appends; it does not clobber the first line."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    main(["harvest", str(harvest_repo), "--label", cid, "keep"])
    main(["harvest", str(harvest_repo), "--label", cid, "discard"])
    assert len(labels.read_text(encoding="utf-8").splitlines()) == 2


def test_harvest_precision_reports_at_n_and_lift(harvest_repo, tmp_path, capsys, monkeypatch):
    """--precision reads labels.jsonl, reports precision@N, base rate and lift."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    assert main(["harvest", str(harvest_repo), "--label", cid, "keep"]) == 0
    capsys.readouterr()

    assert main(["harvest", str(harvest_repo), "--precision"]) == 0
    out = capsys.readouterr().out.lower()
    assert "precision@" in out
    assert "base rate" in out
    assert "lift" in out
    assert "1 labeled" in out  # exactly one label recorded


def test_harvest_precision_no_labels_is_friendly(harvest_repo, tmp_path, capsys, monkeypatch):
    """With no labels yet, --precision explains how to start and exits 0."""
    import scar.cli as cli
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", tmp_path / "labels.jsonl")
    assert main(["harvest", str(harvest_repo), "--precision"]) == 0
    out = capsys.readouterr().out.lower()
    assert "no labels" in out
    assert "--label" in out


def test_harvest_precision_at_override(harvest_repo, tmp_path, capsys, monkeypatch):
    """--at overrides the default N set."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    main(["harvest", str(harvest_repo), "--label", cid, "keep"])
    capsys.readouterr()
    assert main(["harvest", str(harvest_repo), "--precision", "--at", "1"]) == 0
    out = capsys.readouterr().out
    assert "precision@1" in out
    assert "precision@5" not in out  # default set suppressed by override


def test_harvest_label_rejects_unknown_id(harvest_repo, tmp_path, capsys, monkeypatch):
    """An id not present in the current harvest is rejected; nothing appended."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    assert main(["harvest", str(harvest_repo), "--label", "deadbeef00", "keep"]) == 1
    out = capsys.readouterr().out
    assert "not a harvest candidate" in out.lower() or "unknown" in out.lower()
    assert not labels.exists()


def test_harvest_label_rejects_bogus_label(harvest_repo, tmp_path, capsys, monkeypatch):
    """Only keep/discard are valid; a third value is rejected (precision_at_n
    contract depends on exactly these two)."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    cid = _first_candidate_id(harvest_repo)
    assert main(["harvest", str(harvest_repo), "--label", cid, "maybe"]) == 1
    out = capsys.readouterr().out
    assert "keep" in out.lower() and "discard" in out.lower()
    assert not labels.exists()


def test_harvest_label_date_is_monkeypatchable(harvest_repo, tmp_path, monkeypatch):
    """The date field comes from time.strftime, monkeypatchable for determinism."""
    import scar.cli as cli
    labels = tmp_path / "labels.jsonl"
    monkeypatch.setattr(cli, "LABELS_PATH_OVERRIDE", labels)
    monkeypatch.setattr(cli.time, "strftime", lambda fmt: "1999-12-31")
    cid = _first_candidate_id(harvest_repo)
    main(["harvest", str(harvest_repo), "--label", cid, "keep"])
    rec = json.loads(labels.read_text(encoding="utf-8").splitlines()[0])
    assert rec["date"] == "1999-12-31"
