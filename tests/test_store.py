"""ScarStore: discovery, listing, id assignment, promote, init."""

from pathlib import Path

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


def test_promote_preserves_violation_field(repo):
    # Task 1: promote goes through parse_scar_text + Scar.to_text() — the
    # same landmine class as scar #4 (expires/evidence silently dropped by
    # a field-wise rewrite). Verify violation survives that roundtrip.
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    text = CANDIDATE.replace("status: candidate", 'violation: "shutil\\.which"\nstatus: candidate')
    cand.write_text(text)
    store = ScarStore.discover(repo)
    new_path = store.promote(cand, reviewer="kibukx")
    promoted_text = new_path.read_text()
    assert 'violation: "shutil\\.which"' in promoted_text


def test_promote_refuses_scar_with_lint_errors(repo):
    cand = repo / ".scars" / "candidates" / "bad.md"
    cand.write_text("# not a scar\n")
    store = ScarStore.discover(repo)
    with pytest.raises(ValueError, match="lint"):
        store.promote(cand, reviewer="kibukx")


def test_scars_for_path_is_bidirectional(repo):
    (repo / ".scars" / "0001-x.deadend.md").write_text(
        CANDIDATE.replace("status: candidate", "status: active").replace(
            "type: deadend", "type: deadend").replace("title: Tried X, failed",
                                                      "title: Tried X, failed") )
    store = ScarStore.discover(repo)
    # query under the anchor (src/deep under src/) AND anchor under the query (root)
    assert store.scars_for_path("src/deep/file.py")
    assert store.scars_for_path("")  # repo root: every anchor is under it
    assert not store.scars_for_path("docs/readme.md")


def test_template_documents_durable_evidence_forms():
    from scar.store import TEMPLATE
    assert "issue:" in TEMPLATE
    assert "url:" in TEMPLATE
    assert "squash" in TEMPLATE.lower()


def test_template_with_durable_forms_parses_clean():
    from scar.store import TEMPLATE
    from scar.model import parse_scar_text
    text = TEMPLATE.replace("status: template", "status: active")
    s = parse_scar_text(text)
    assert any(e.startswith("issue:") or e.startswith("url:") for e in s.evidence)


# --- reviewer dedup is case-insensitive (#182) ---

DUP_AUTHOR_CANDIDATE = CANDIDATE.replace(
    'authors: ["claude-code"]', 'authors: ["claude-code", "kibukx"]')


def test_promote_writes_the_reviewer_to_promoted_by_and_leaves_authors_alone(repo):
    """#287, superseding the #182 append-and-dedup. The reviewer is who
    VOUCHED, and that is a different role from who drafted, so it gets its
    own key. authors: is left exactly as the candidate wrote it."""
    cand = repo / ".scars" / "candidates" / "dup-author.md"
    cand.write_text(DUP_AUTHOR_CANDIDATE)
    store = ScarStore.discover(repo)
    new_path = store.promote(cand, reviewer="mara")
    text = new_path.read_text()
    assert "promoted_by: mara" in text
    assert 'authors: ["claude-code", "kibukx"]' in text


def test_promote_no_longer_dedups_the_reviewer_against_authors(repo):
    """The #182 silent-skip: a reviewer whose handle was already in authors
    (an agent can write any name there) made the promotion append a no-op,
    so the promoted file could not show who vouched. With its own key the
    reviewer is recorded whatever authors: says."""
    cand = repo / ".scars" / "candidates" / "dup-author.md"
    cand.write_text(DUP_AUTHOR_CANDIDATE)
    store = ScarStore.discover(repo)
    new_path = store.promote(cand, reviewer="Kibukx")
    text = new_path.read_text()
    assert "promoted_by: Kibukx" in text
    assert 'authors: ["claude-code", "kibukx"]' in text


def test_promote_overwrites_a_pre_seeded_promoted_by(repo):
    """A candidate cannot have been promoted, so a promoted_by it carries is
    a claim nobody made. Promote overwrites it with the actual reviewer
    rather than trusting or appending to it."""
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE.replace('authors: ["claude-code"]',
                                      'authors: ["claude-code"]\npromoted_by: kibukx'))
    store = ScarStore.discover(repo)
    new_path = store.promote(cand, reviewer="mara")
    text = new_path.read_text()
    assert "promoted_by: mara" in text
    assert "kibukx" not in text


def test_template_documents_revives_if():
    """revives_if (#205) must be discoverable where authors actually look —
    the template — or the field ships unused."""
    from scar.store import TEMPLATE
    root = Path(__file__).parent.parent
    assert "revives_if" in TEMPLATE
    assert "revives_if" in (root / ".scars" / "template.md").read_text()
