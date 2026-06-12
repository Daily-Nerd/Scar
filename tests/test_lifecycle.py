"""Lifecycle v0 (#14): challenge, archive, review_after surfacing.

Contract: challenged scars still fire (marked), archived never fire,
expiry dates surface as warnings — never auto-archive (ADR-4 governance).
"""

import pytest

from scar.cli import main
from scar.lint import lint_text
from scar.match import rank_for_edit
from scar.store import ScarStore, init_scars

ACTIVE = """\
---
id: 1
type: deadend
title: Tried X, failed
severity: high
confidence: 0.9
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/
evidence:
  - commit: abc1234
expires:
  condition: "X becomes viable"
  review_after: 2026-01-01
status: active
---

Why X failed.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-tried-x.deadend.md").write_text(ACTIVE)
    return tmp_path


def store_of(repo):
    return ScarStore.discover(repo)


# --- store.transition ---

def test_challenge_flips_status_and_records_reason(repo):
    store = store_of(repo)
    path = store.transition(1, "challenged", reason="X works since v9", date="2026-06-12")
    text = path.read_text()
    assert "status: challenged" in text
    assert "challenged 2026-06-12: X works since v9" in text
    # same file, in place — archived/challenged scars keep their identity
    assert path.name == "0001-tried-x.deadend.md"


def test_archive_flips_status_and_records_reason(repo):
    store = store_of(repo)
    path = store.transition(1, "archived", reason="condition met", date="2026-06-12")
    assert "status: archived" in path.read_text()


def test_transition_unknown_id_raises(repo):
    with pytest.raises(ValueError):
        store_of(repo).transition(99, "archived", reason="r", date="2026-06-12")


def test_transition_to_same_status_raises(repo):
    store = store_of(repo)
    store.transition(1, "challenged", reason="r", date="2026-06-12")
    with pytest.raises(ValueError):
        store.transition(1, "challenged", reason="again", date="2026-06-12")


# --- firing semantics ---

def test_challenged_scar_still_fires(repo):
    store = store_of(repo)
    store.transition(1, "challenged", reason="r", date="2026-06-12")
    hits = rank_for_edit(store, repo / "src" / "x.py", "")
    assert [s.id for s in hits] == [1]


def test_archived_scar_never_fires(repo):
    store = store_of(repo)
    store.transition(1, "archived", reason="r", date="2026-06-12")
    assert rank_for_edit(store, repo / "src" / "x.py", "") == []


# --- CLI ---

def test_cli_challenge_and_marker_in_check(repo, capsys):
    assert main(["challenge", "1", "--reason", "X works since v9"]) == 0
    capsys.readouterr()
    assert main(["check", str(repo / "src")]) == 0
    out = capsys.readouterr().out
    assert "challenged" in out and "#1" in out


def test_cli_archive_silences_check(repo, capsys):
    assert main(["archive", "1", "--reason", "condition met"]) == 0
    capsys.readouterr()
    assert main(["check", str(repo / "src")]) == 0
    assert "no scars anchored" in capsys.readouterr().out


def test_cli_challenge_unknown_id_fails(repo, capsys):
    assert main(["challenge", "7", "--reason", "r"]) == 1


# --- review_after surfacing ---

def test_lint_warns_on_past_review_after():
    findings = lint_text(ACTIVE, today="2026-06-12")
    assert any("review_after" in f.message and f.level == "warning" for f in findings)


def test_lint_silent_on_future_review_after():
    findings = lint_text(ACTIVE, today="2025-06-12")
    assert not any("review_after" in f.message for f in findings)


def test_lint_silent_on_archived_past_review_after():
    text = ACTIVE.replace("status: active", "status: archived")
    findings = lint_text(text, today="2026-06-12")
    assert not any("review_after" in f.message for f in findings)


def test_status_lists_review_due(repo, capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "review due" in out.lower() and "#1" in out
