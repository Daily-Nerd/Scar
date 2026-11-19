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


@pytest.mark.parametrize("raw,expected", [
    ("Reverted: retry_count failed", "Reverted: retry_count failed"),
    ("Reverted: fix `retry_count` reset", "Reverted: fix retry_count reset"),
    ("a `x_*_y` b", "a x_*_y b"),
    ("**bold** and _em_ ok", "bold and em ok"),
    ("Redis *failed* [here](http://x.io)", "Redis failed here"),
    ("see https://x.io/a_b  `code`", "see code"),
    ("ünï", "ünï"),
])
def test_sanitize_signal_text_keeps_identifiers_and_code_spans(raw, expected):
    """Display cleaning removes emphasis markers only where they can be
    emphasis delimiters. An underscore between two word characters is part of
    an identifier (CommonMark intraword rule) and code-span content is kept
    verbatim (#402). Supersedes the #365-era pin that expected `retrycount`."""
    from scar.store import sanitize_signal_text
    assert sanitize_signal_text(raw) == expected


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


# --- #377: lifecycle transition table owns every status change ---

from scar.model import STATUSES  # noqa: E402
from scar.store import LIFECYCLE_TRANSITIONS  # noqa: E402

NON_CANDIDATE = [s for s in STATUSES if s != "candidate"]


def _snapshot(repo):
    return {p: p.read_bytes() for p in (repo / ".scars").rglob("*.md")}


@pytest.mark.parametrize("status", NON_CANDIDATE)
def test_promote_refuses_every_non_candidate_status(repo, status):
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE.replace("status: candidate", f"status: {status}"))
    store = ScarStore.discover(repo)
    before, next_id = _snapshot(repo), store.next_id()
    with pytest.raises(ValueError) as exc:
        store.promote(cand, reviewer="kibukx")
    assert "tried-x.md" in str(exc.value) and status in str(exc.value)
    assert _snapshot(repo) == before
    assert store.next_id() == next_id


def test_promote_refuses_a_file_outside_candidates(repo):
    stray = repo / ".scars" / "stray.md"
    stray.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    before, next_id = _snapshot(repo), store.next_id()
    with pytest.raises(ValueError, match="stray.md"):
        store.promote(stray, reviewer="kibukx")
    assert _snapshot(repo) == before
    assert store.next_id() == next_id


def test_transition_rejects_a_move_the_table_does_not_allow(repo):
    f = repo / ".scars" / "0001-x.deadend.md"
    f.write_text(CANDIDATE.replace("status: candidate", "status: archived").replace(
        "type: deadend", "id: 1\ntype: deadend"))
    store = ScarStore.discover(repo)
    before = f.read_bytes()
    with pytest.raises(ValueError, match="archived"):
        store.transition(1, "challenged", reason="r", date="2026-06-12")
    assert f.read_bytes() == before


def test_table_only_names_known_statuses_and_archived_is_terminal():
    for src, dsts in LIFECYCLE_TRANSITIONS.items():
        assert src in STATUSES and set(dsts) <= set(STATUSES)
    assert not LIFECYCLE_TRANSITIONS.get("archived")


def test_archive_candidate_keeps_file_records_reason_and_blocks_promote(repo):
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    path = store.archive_candidate(cand, reason="obsolete", date="2026-06-12")
    assert path == cand and cand.exists()
    text = cand.read_text()
    assert "status: archived" in text and "archived 2026-06-12: obsolete" in text
    with pytest.raises(ValueError, match="archived"):
        store.promote(cand, reviewer="kibukx")
    assert not list((repo / ".scars").glob("0*.md"))
    assert store.pending_candidates() == []
    assert store.candidates() == [cand]


# --- #377 follow-up: destination collisions are planned before any write ---

def test_plan_promotions_reports_a_colliding_destination_and_writes_nothing(repo):
    cdir = repo / ".scars" / "candidates"
    (cdir / "one.md").write_text(CANDIDATE)
    (cdir / "two.md").write_text(CANDIDATE)
    # An unparseable numbered file is invisible to next_id() but owns the name.
    (repo / ".scars" / "0002-two.deadend.md").write_text("# not a scar\n")
    store = ScarStore.discover(repo)
    before = _snapshot(repo)
    dests, errors = store.plan_promotions([cdir / "one.md", cdir / "two.md"])
    assert len(errors) == 1 and "two.md" in errors[0]
    assert _snapshot(repo) == before


def test_plan_promotions_gives_a_batch_distinct_destinations(repo):
    cdir = repo / ".scars" / "candidates"
    (cdir / "one.md").write_text(CANDIDATE)
    (cdir / "two.md").write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    dests, errors = store.plan_promotions([cdir / "one.md", cdir / "two.md"])
    assert errors == [] and len(set(dests)) == 2
    assert [d.name.split("-")[0] for d in dests] == ["0001", "0002"]


def test_unparseable_candidate_has_its_own_error_type(repo):
    from scar.store import UnparseableCandidate
    bad = repo / ".scars" / "candidates" / "bad.md"
    bad.write_text("# not a scar\n")
    store = ScarStore.discover(repo)
    with pytest.raises(UnparseableCandidate):
        store._parse_candidate_file(bad)
    assert issubclass(UnparseableCandidate, ValueError)
    with pytest.raises(ValueError):  # promote still reports it, as lint errors
        store.promote(bad, reviewer="k")


def test_promote_records_the_candidate_path_in_promoted_from(repo):
    """The link from an active scar to its candidate is written down, so a
    reader never re-derives active_slug/slugify to find it."""
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    text = store.promote(cand, reviewer="mara").read_text()
    assert "promoted_from: candidates/tried-x.md" in text


def test_promote_overwrites_a_pre_seeded_promoted_from(repo):
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE.replace('authors: ["claude-code"]',
                                      'authors: ["claude-code"]\npromoted_from: candidates/other.md'))
    store = ScarStore.discover(repo)
    text = store.promote(cand, reviewer="mara").read_text()
    assert "promoted_from: candidates/tried-x.md" in text
    assert "other.md" not in text


# #392: promote is prepare (no I/O) then commit (exclusive create, then unlink),
# so a signing step can sit between the two without a half-promoted state.

def test_prepare_promotion_touches_nothing_and_uses_no_id(repo):
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    before, next_id = _snapshot(repo), store.next_id()
    prepared = store.prepare_promotion(cand, reviewer="kibukx", reviewer_source="explicit")
    assert _snapshot(repo) == before
    assert store.next_id() == next_id
    assert prepared.scar.id == next_id
    assert prepared.scar.status == "active"
    assert prepared.scar.promoted_by == "kibukx"
    assert prepared.scar.promoted_from == "candidates/tried-x.md"
    assert prepared.destination.name == "0001-tried-x.deadend.md"
    assert not prepared.destination.exists()


def test_prepare_promotion_refuses_what_promote_refuses(repo):
    stray = repo / ".scars" / "stray.md"
    stray.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    before = _snapshot(repo)
    with pytest.raises(ValueError, match="stray.md"):
        store.prepare_promotion(stray, reviewer="k")
    assert _snapshot(repo) == before


def test_commit_promotion_writes_the_prepared_scar_and_removes_the_candidate(repo):
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    prepared = store.prepare_promotion(cand, reviewer="kibukx")
    path = store.commit_promotion(prepared)
    assert path == prepared.destination
    assert path.read_text(encoding="utf-8") == prepared.scar.to_text()
    assert not cand.exists()


def test_promote_is_prepare_then_commit_byte_for_byte(repo):
    text = CANDIDATE
    a = repo / ".scars" / "candidates" / "tried-x.md"
    a.write_text(text)
    store = ScarStore.discover(repo)
    expected = store.prepare_promotion(a, reviewer="kibukx", reviewer_source="explicit")
    expected_bytes = expected.scar.to_text().encode("utf-8")
    path = store.promote(a, reviewer="kibukx", reviewer_source="explicit")
    assert path.read_bytes() == expected_bytes


def test_commit_promotion_never_overwrites_a_file_that_appeared_after_prepare(repo):
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE)
    store = ScarStore.discover(repo)
    prepared = store.prepare_promotion(cand, reviewer="kibukx")
    prepared.destination.write_text("precious", encoding="utf-8")
    with pytest.raises(ValueError, match="exists"):
        store.commit_promotion(prepared)
    assert prepared.destination.read_text(encoding="utf-8") == "precious"
    assert cand.read_text(encoding="utf-8") == CANDIDATE


def test_promotion_overwrites_an_act_and_receipt_the_candidate_carried(repo):
    # Like promoted_by: a candidate cannot have been signed, so an act it
    # carries is a claim nobody made. Only the signing step may set these.
    cand = repo / ".scars" / "candidates" / "tried-x.md"
    cand.write_text(CANDIDATE.replace(
        "status: candidate", "promotion_act: forged.jws.here\npromotion_receipt: uEiForged\nstatus: candidate"))
    store = ScarStore.discover(repo)
    prepared = store.prepare_promotion(cand, reviewer="kibukx")
    assert prepared.scar.promotion_act == ""
    assert prepared.scar.promotion_receipt == ""
    text = store.commit_promotion(prepared).read_text(encoding="utf-8")
    assert "promotion_act" not in text and "promotion_receipt" not in text


# #392: every scar's receipt, archived included, feeds the checker's `seen`
# list so one act cannot be reused for a second scar.

def _scar_with_receipt(repo, name, receipt, status):
    text = (f"---\nid: {int(name[:4])}\ntype: fence\ntitle: T\nseverity: low\nconfidence: 0.5\n"
            "promoted_by: k\npromotion_act: a.b.c\n"
            + (f"promotion_receipt: {receipt}\n" if receipt else "")
            + f"anchors:\n  - path: src/x.py\nstatus: {status}\n---\n\nBody.\n")
    (repo / ".scars" / name).write_text(text)


def test_seen_receipts_include_every_status_archived_too(repo):
    _scar_with_receipt(repo, "0001-a.fence.md", "uEiA", "active")
    _scar_with_receipt(repo, "0002-b.fence.md", "uEiB", "archived")
    _scar_with_receipt(repo, "0003-c.fence.md", "uEiC", "orphaned")
    _scar_with_receipt(repo, "0004-d.fence.md", "uEiD", "challenged")
    _scar_with_receipt(repo, "0005-e.fence.md", "", "active")
    store = ScarStore.discover(repo)
    assert store.seen_receipts() == ["uEiA", "uEiB", "uEiC", "uEiD"]


def test_seen_receipts_are_unique_and_empty_for_a_fresh_store(repo):
    store = ScarStore.discover(repo)
    assert store.seen_receipts() == []
    _scar_with_receipt(repo, "0001-a.fence.md", "uEiSame", "active")
    _scar_with_receipt(repo, "0002-b.fence.md", "uEiSame", "active")
    assert store.seen_receipts() == ["uEiSame"]
