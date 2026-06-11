"""ScarStore: discovery, listing, id assignment, promote, init."""

import pytest

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
def repo(tmp_path):
    (tmp_path / ".git").mkdir()
    init_scars(tmp_path)
    return tmp_path


def test_init_creates_dir_readme_template_candidates(repo):
    scars = repo / ".scars"
    assert scars.is_dir()
    assert (scars / "README.md").exists()
    assert (scars / "template.md").exists()
    assert (scars / "candidates").is_dir()


def test_init_is_idempotent_and_does_not_clobber(repo):
    readme = repo / ".scars" / "README.md"
    readme.write_text("customized")
    init_scars(repo)
    assert readme.read_text() == "customized"


def test_discover_walks_up_from_subdir(repo):
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    store = ScarStore.discover(sub)
    assert store is not None and store.root == repo


def test_discover_stops_at_git_root_without_scars(tmp_path):
    (tmp_path / ".git").mkdir()
    assert ScarStore.discover(tmp_path) is None


def test_template_and_readme_excluded_from_listing(repo):
    store = ScarStore.discover(repo)
    assert store.active() == []
    assert store.broken() == []


def test_unparseable_file_listed_as_broken_not_skipped(repo):
    (repo / ".scars" / "0009-bad.deadend.md").write_text("# no frontmatter\n")
    store = ScarStore.discover(repo)
    assert [p.name for p in store.broken()] == ["0009-bad.deadend.md"]


def test_next_id_starts_at_1_and_follows_max(repo):
    store = ScarStore.discover(repo)
    assert store.next_id() == 1
    (repo / ".scars" / "0007-x.fence.md").write_text(
        CANDIDATE.replace("status: candidate", "status: active").replace(
            "type: deadend", "id: 7\ntype: fence"))
    assert ScarStore.discover(repo).next_id() == 8


def test_promote_moves_assigns_id_status_reviewer(repo):
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    new_path = store.promote(cand, reviewer="kibukx")
    assert not cand.exists()
    assert new_path.name == "0001-tried-x.deadend.md"
    text = new_path.read_text()
    assert "id: 1" in text and "status: active" in text and "kibukx" in text


def test_promote_refuses_scar_with_lint_errors(repo):
    cand = repo / ".scars" / "candidates" / "bad.md"
    cand.write_text("# not a scar\n")
    store = ScarStore.discover(repo)
    with pytest.raises(ValueError, match="lint"):
        store.promote(cand, reviewer="kibukx")
