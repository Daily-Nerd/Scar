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


def test_promote_with_explicit_reviewer_source_writes_explicit(repo):
    """#295: the CLI computes whether the reviewer name was typed or read
    from git config and hands it to promote as reviewer_source; explicit
    writes 'explicit'."""
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    new_path = store.promote(cand, reviewer="mara", reviewer_source="explicit")
    text = new_path.read_text()
    assert "promoted_by_source: explicit" in text


def test_promote_with_git_config_reviewer_source_writes_git_config(repo):
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    new_path = store.promote(cand, reviewer="mara", reviewer_source="git-config")
    text = new_path.read_text()
    assert "promoted_by_source: git-config" in text


def test_promote_without_a_reviewer_writes_neither_key(repo):
    """Old behavior: an unset git identity means nobody vouched, so neither
    promoted_by nor promoted_by_source belongs on the file."""
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    new_path = store.promote(cand, reviewer="", reviewer_source="git-config")
    text = new_path.read_text()
    assert "promoted_by:" not in text
    assert "promoted_by_source:" not in text


def test_template_documents_revives_if():
    """revives_if (#205) must be discoverable where authors actually look —
    the template — or the field ships unused."""
    from scar.store import TEMPLATE
    root = Path(__file__).parent.parent
    assert "revives_if" in TEMPLATE
    assert "revives_if" in (root / ".scars" / "template.md").read_text()


# --- append_evidence_note (#296): reanchor --apply's write path, reusing
# transition()'s parse -> append -> reserialize mechanism instead of a
# second YAML splicer. ---

WITH_EVIDENCE = """\
---
id: 1
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
status: active
---

Why X failed.
"""

NO_EVIDENCE = """\
---
id: 2
type: deadend
title: Tried Y, failed
severity: medium
confidence: 0.7
created: 2026-06-10
authors: ["claude-code"]
anchors:
  - path: src/y.py
status: active
---

Why Y failed.
"""

_NOTE = 'note: "reanchored 2026-09-02: path a -> b (tier: high)"'


def test_append_evidence_note_keeps_existing_entries(repo):
    (repo / ".scars" / "0001-x.deadend.md").write_text(WITH_EVIDENCE)
    store = ScarStore.discover(repo)
    path = store.append_evidence_note(1, _NOTE)
    text = path.read_text()
    assert "  - commit: abc1234" in text
    assert f"  - {_NOTE}" in text


def test_append_evidence_note_creates_evidence_block_when_absent(repo):
    (repo / ".scars" / "0002-y.deadend.md").write_text(NO_EVIDENCE)
    store = ScarStore.discover(repo)
    path = store.append_evidence_note(2, _NOTE)
    text = path.read_text()
    assert "evidence:" in text
    assert f"  - {_NOTE}" in text


def test_append_evidence_note_survives_parse_round_trip(repo):
    (repo / ".scars" / "0002-y.deadend.md").write_text(NO_EVIDENCE)
    ScarStore.discover(repo).append_evidence_note(2, _NOTE)
    reparsed = ScarStore.discover(repo)
    _, scar = next((f, s) for f, s in reparsed.parsed() if s.id == 2)
    # parse_scar_text unquotes evidence values the same way it does for
    # every other quoted field (pattern/violation/url): the wrapper is a
    # serialization detail, the note text itself is what must survive.
    assert 'note: reanchored 2026-09-02: path a -> b (tier: high)' in scar.evidence


def test_append_evidence_note_unknown_id_raises(repo):
    with pytest.raises(ValueError):
        ScarStore.discover(repo).append_evidence_note(99, _NOTE)


# --- #352: one canonical active filename -----------------------------------

def _cand(repo, name, type_="landmine"):
    p = repo / ".scars" / "candidates" / name
    p.write_text(CANDIDATE.replace("type: deadend", f"type: {type_}"),
                 encoding="utf-8")
    return p


def test_promote_strips_type_extension_leak(repo):
    out = ScarStore.discover(repo).promote(_cand(repo, "x.landmine.md"), reviewer="k")
    assert out.name == "0001-x.landmine.md"


def test_promote_strips_extension_chain_for_any_type(repo):
    out = ScarStore.discover(repo).promote(_cand(repo, "x.fence.deadend.md"), reviewer="k")
    assert out.name == "0001-x.landmine.md"


def test_promote_strips_own_type_dash_suffix(repo):
    out = ScarStore.discover(repo).promote(_cand(repo, "foo-bar-landmine.md"), reviewer="k")
    assert out.name == "0001-foo-bar.landmine.md"


def test_promote_keeps_other_type_dash_suffix(repo):
    out = ScarStore.discover(repo).promote(_cand(repo, "x-fence.md"), reviewer="k")
    assert out.name == "0001-x-fence.landmine.md"


def test_promote_plain_name_unchanged(repo):
    out = ScarStore.discover(repo).promote(_cand(repo, "plain.md"), reviewer="k")
    assert out.name == "0001-plain.landmine.md"


def test_promoted_name_has_exactly_one_type_suffix(repo):
    import re
    for n in ["a.landmine.md", "b-landmine.md", "c.fence.md",
              "d-landmine.landmine.md"]:
        out = ScarStore.discover(repo).promote(_cand(repo, n), reviewer="k")
        assert len(re.findall(r"\.(deadend|fence|landmine)", out.name)) == 1


def test_promote_sanitises_and_caps_slug(repo):
    long = "Weird *Name* " + "word-" * 30
    out = ScarStore.discover(repo).promote(_cand(repo, long + ".md"), reviewer="k")
    slug = out.name[len("0001-"):-len(".landmine.md")]
    assert len(slug) <= 60
    assert slug == slug.lower() and " " not in slug and "*" not in slug


def test_promote_refuses_to_overwrite_existing_active_file(repo, monkeypatch):
    store = ScarStore.discover(repo)
    existing = repo / ".scars" / "0001-x.landmine.md"
    existing.write_text("precious", encoding="utf-8")
    monkeypatch.setattr(ScarStore, "next_id", lambda self: 1)
    cand = _cand(repo, "x.landmine.md")
    with pytest.raises(ValueError, match="exists"):
        store.promote(cand, reviewer="k")
    assert existing.read_text(encoding="utf-8") == "precious"
    assert cand.exists()


def test_shared_slugify_is_the_harvest_and_mcp_normaliser():
    from scar.store import slugify
    assert slugify("Redis *failed* [here](http://x.io)", fallback="f") == "redis-failed-here"
    assert slugify("", fallback="signal") == "signal"
    assert len(slugify("word " * 40, fallback="f")) <= 60


@pytest.mark.parametrize("raw,expected", [
    ("my_file_name_landmine", "my-file-name-landmine"),
    ("ünï-cödé", "uni-code"),
    ("Ordem de importação", "ordem-de-importacao"),
    ("retry *wrapper* in `foo_bar`", "retry-wrapper-in-foo-bar"),
    ("See [my_doc](http://x.io/a_b) now", "see-my-doc-now"),
    ("https://example.com/a_b/c plain_word", "plain-word"),
])
def test_slugify_keeps_separators_and_transliterates(raw, expected):
    """A filename slug treats `_`, `*` and backticks as word separators and
    maps accented letters to their ASCII base (#365)."""
    from scar.store import slugify
    assert slugify(raw, fallback="f") == expected


def test_slugify_falls_back_when_nothing_decomposes_to_ascii():
    from scar.store import slugify
    assert slugify("日本語のタイトル", fallback="fb") == "fb"


def test_sanitize_signal_text_is_unchanged_by_the_slug_fix():
    """The display-text cleaner keeps deleting emphasis markers (its own job,
    #159). `retry_count` -> `retrycount` is a known sibling of #365, left alone
    on purpose: it is a title, not a filename."""
    from scar.store import sanitize_signal_text
    assert sanitize_signal_text("retry_count failed") == "retrycount failed"
    assert sanitize_signal_text("Redis *failed* [here](http://x.io)") == "Redis failed here"
    assert sanitize_signal_text("see https://x.io/a_b  `code`") == "see code"
    assert sanitize_signal_text("ünï") == "ünï"


def test_promote_keeps_underscores_as_separators(repo):
    out = ScarStore.discover(repo).promote(
        _cand(repo, "my_file_name.landmine.md"), reviewer="k")
    assert out.name == "0001-my-file-name.landmine.md"


# --- one owner for candidates/ (#370) ---

def test_init_writes_empty_gitkeep_that_is_not_a_candidate(repo):
    keep = repo / ".scars" / "candidates" / ".gitkeep"
    assert keep.is_file() and keep.read_text() == ""
    store = ScarStore.discover(repo)
    assert store.candidates() == []
    assert store.broken() == []


def test_ensure_candidates_dir_recreates_missing_folder_idempotently(repo):
    import shutil
    shutil.rmtree(repo / ".scars" / "candidates")
    store = ScarStore.discover(repo)
    path = store.ensure_candidates_dir()
    assert path == repo / ".scars" / "candidates" and path.is_dir()
    assert store.ensure_candidates_dir() == path
