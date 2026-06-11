"""CLI surface: each command's contract, exercised through main()."""

import json

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


def test_inject_silent_when_no_match(repo, capsys):
    init_scars(repo)
    assert main(["inject", "--path", "docs/x.md", "--content", ""]) == 0
    assert capsys.readouterr().out.strip() == ""


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
