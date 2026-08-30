"""Firing-count review triggers (#274)."""

from __future__ import annotations

import json
import subprocess

import pytest

from scar.model import parse_scar_text
from scar.review import (
    DEFAULT_THRESHOLDS,
    FiringReview,
    firing_reviews,
    firing_timestamps,
    last_revised,
    threshold_for,
)
from scar.store import ScarStore


def _scar_text(scar_id: int, type_: str = "landmine", extra: str = "") -> str:
    return (
        "---\n"
        f"id: {scar_id}\n"
        f"type: {type_}\n"
        f"title: scar {scar_id}\n"
        "severity: medium\n"
        "confidence: 0.7\n"
        "anchors:\n"
        "  - path: src/\n"
        "evidence:\n"
        "  - issue: 274\n"
        f"{extra}"
        "status: active\n"
        "---\n\n"
        "Body.\n"
    )


# --- the field ------------------------------------------------------------

def test_field_parses_as_int():
    scar = parse_scar_text(_scar_text(
        1, extra="expires:\n  review_after_firings: 25\n"))
    assert scar.review_after_firings == 25


def test_absent_field_is_none_not_zero():
    # None falls back to the per-type default; 0 is an explicit opt-out. If
    # absence parsed as 0, no landmine could ever escalate.
    assert parse_scar_text(_scar_text(1)).review_after_firings is None


def test_explicit_zero_disables_escalation():
    scar = parse_scar_text(_scar_text(
        1, extra="expires:\n  review_after_firings: 0\n"))
    assert scar.review_after_firings == 0
    assert threshold_for(scar) == 0


def test_unparseable_value_does_not_become_zero():
    # Coercing garbage to 0 would silently disarm the trigger the author was
    # trying to set. None falls back to the type default instead.
    scar = parse_scar_text(_scar_text(
        1, extra="expires:\n  review_after_firings: soon\n"))
    assert scar.review_after_firings is None
    assert threshold_for(scar) == DEFAULT_THRESHOLDS["landmine"]


def test_negative_value_falls_back_to_default():
    scar = parse_scar_text(_scar_text(
        1, extra="expires:\n  review_after_firings: -3\n"))
    assert scar.review_after_firings is None


@pytest.mark.parametrize("type_,expected", [
    ("landmine", 10), ("fence", 15), ("deadend", 0)])
def test_type_defaults(type_, expected):
    assert threshold_for(parse_scar_text(_scar_text(1, type_))) == expected


def test_explicit_field_overrides_the_type_default():
    scar = parse_scar_text(_scar_text(
        1, "deadend", extra="expires:\n  review_after_firings: 3\n"))
    assert threshold_for(scar) == 3


# --- the prefix collision that would be silent ----------------------------

def test_review_after_firings_does_not_capture_review_after():
    """review_after: is a DATE and review_after_firings: is a COUNT. _field
    anchors on '<name>:', so neither may match the other's line — a cross-match
    here voids one of the two triggers with no error anywhere (#69 class)."""
    only_date = parse_scar_text(_scar_text(
        1, extra="expires:\n  review_after: 1971-01-01\n"))
    assert only_date.review_after == "1971-01-01"
    assert only_date.review_after_firings is None

    only_count = parse_scar_text(_scar_text(
        1, extra="expires:\n  review_after_firings: 8\n"))
    assert only_count.review_after == ""
    assert only_count.review_after_firings == 8

    both = parse_scar_text(_scar_text(
        1, extra="expires:\n  review_after: 1971-01-01\n  review_after_firings: 8\n"))
    assert both.review_after == "1971-01-01"
    assert both.review_after_firings == 8


def test_roundtrip_preserves_the_field():
    # Promote/archive round-trips through to_text; a dropped field here is the
    # #4 failure (expires/evidence silently lost on transition).
    original = parse_scar_text(_scar_text(
        1, extra="expires:\n  condition: \"gone\"\n  review_after_firings: 4\n"))
    assert parse_scar_text(original.to_text()).review_after_firings == 4


def test_roundtrip_emits_expires_block_for_the_field_alone():
    original = parse_scar_text(_scar_text(
        1, extra="expires:\n  review_after_firings: 4\n"))
    text = original.to_text()
    assert "expires:" in text
    assert parse_scar_text(text).review_after_firings == 4


# --- counting -------------------------------------------------------------

def test_counts_only_this_repo():
    records = [
        {"repo": "/a", "ts": "2026-01-01T00:00:00", "scar_ids": [1]},
        {"repo": "/b", "ts": "2026-01-01T00:00:00", "scar_ids": [1]},
    ]
    dated, _ = firing_timestamps(records, "/a")
    assert dated == {1: ["2026-01-01T00:00:00"]}


def test_violation_rows_are_not_firings():
    records = [{"repo": "/a", "ts": "2026-01-01T00:00:00", "violation_ids": [1]}]
    dated, undated = firing_timestamps(records, "/a")
    assert dated == {} and undated == {}


def test_undated_rows_are_counted_separately_not_dropped():
    records = [
        {"repo": "/a", "scar_ids": [1]},
        {"repo": "/a", "ts": None, "scar_ids": [1]},
    ]
    dated, undated = firing_timestamps(records, "/a")
    assert dated == {} and undated == {1: 2}


def test_malformed_rows_are_tolerated():
    # landmine #12: any JSON shape reaches this log.
    records = [None, [], 3, {"repo": "/a", "scar_ids": "nope"},
               {"repo": "/a", "ts": "2026-01-01T00:00:00", "scar_ids": [1, "x", None]}]
    dated, _ = firing_timestamps(records, "/a")
    assert dated == {1: ["2026-01-01T00:00:00"]}


# --- last_revised ---------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


def test_last_revised_is_naive_local_matching_the_log_format(tmp_path):
    """The firing log writes naive LOCAL time. A reset point rendered with a
    UTC offset would compare as a different clock and shift the window."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.md").write_text("x\n")
    _git(tmp_path, "add", "a.md")
    _git(tmp_path, "commit", "-qm", "one")

    out = last_revised(tmp_path, ["a.md"])
    assert set(out) == {"a.md"}
    stamp = out["a.md"]
    assert len(stamp) == 19 and stamp[10] == "T"
    assert "+" not in stamp and "Z" not in stamp


def test_last_revised_takes_the_most_recent_commit(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.md").write_text("x\n")
    _git(tmp_path, "add", "a.md")
    _git(tmp_path, "commit", "-qm", "one", "--date=2020-01-01T00:00:00")
    (tmp_path / "a.md").write_text("y\n")
    _git(tmp_path, "add", "a.md")
    _git(tmp_path, "commit", "-qm", "two")

    assert not last_revised(tmp_path, ["a.md"])["a.md"].startswith("2020")


def test_last_revised_returns_unknown_outside_a_repo(tmp_path):
    # landmine #10: a git failure is NOT an empty result. Returning {} means
    # "no reset point known", which the caller reports as a lifetime count.
    assert last_revised(tmp_path, ["a.md"]) == {}


def test_last_revised_with_no_paths_makes_no_git_call(tmp_path):
    assert last_revised(tmp_path, []) == {}


# --- end to end -----------------------------------------------------------

def _repo_with_scar(tmp_path, scar_id=1, type_="landmine", extra=""):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    scars = tmp_path / ".scars"
    scars.mkdir()
    name = f"{scar_id:04d}-x.{type_}.md"
    (scars / name).write_text(_scar_text(scar_id, type_, extra))
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "scar")
    store = ScarStore.discover(tmp_path)
    assert store is not None
    return store, f".scars/{name}"


def _firings(repo, scar_id, n, ts="2099-01-01T00:00:00"):
    return [{"repo": str(repo), "ts": ts, "scar_ids": [scar_id]} for _ in range(n)]


def test_escalates_at_the_threshold(tmp_path):
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 3\n")
    out = firing_reviews(store, _firings(store.root, 1, 3))
    assert [r.scar_id for r in out] == [1]
    assert out[0].count == 3 and out[0].threshold == 3


def test_below_the_threshold_is_silent(tmp_path):
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 3\n")
    assert firing_reviews(store, _firings(store.root, 1, 2)) == []


def test_firings_before_the_last_revision_do_not_count(tmp_path):
    """Revising a scar re-affirms it, so the count restarts. Firings recorded
    before that commit belong to the previous version of the scar."""
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 2\n")
    old = _firings(store.root, 1, 5, ts="1999-01-01T00:00:00")
    assert firing_reviews(store, old) == []
    assert len(firing_reviews(store, old + _firings(store.root, 1, 2))) == 1


def test_deadend_never_escalates_by_default(tmp_path):
    store, _ = _repo_with_scar(tmp_path, type_="deadend")
    assert firing_reviews(store, _firings(store.root, 1, 500)) == []


def test_archived_scar_is_not_escalated(tmp_path):
    store, rel = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 1\n")
    path = store.root / rel
    path.write_text(path.read_text().replace("status: active", "status: archived"))
    assert firing_reviews(store, _firings(store.root, 1, 50)) == []


def test_untracked_scar_reports_a_lifetime_count_and_says_so(tmp_path):
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 2\n")
    (store.scars_dir / "0002-y.landmine.md").write_text(
        _scar_text(2, extra="expires:\n  review_after_firings: 2\n"))
    out = [r for r in firing_reviews(store, _firings(store.root, 2, 3)) if r.scar_id == 2]
    assert len(out) == 1
    assert out[0].since is None
    assert "lifetime count" in out[0].reason()


def test_other_repos_firings_do_not_escalate_this_one(tmp_path):
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 2\n")
    foreign = [{"repo": "/somewhere/else", "ts": "2099-01-01T00:00:00",
                "scar_ids": [1]} for _ in range(9)]
    assert firing_reviews(store, foreign) == []


def test_reason_names_the_count_the_threshold_and_the_remedy():
    r = FiringReview(scar_id=9, type="landmine", title="t", file="f",
                     count=12, threshold=10, since="2026-08-01T10:00:00")
    text = r.reason()
    assert "12" in text and "10" in text and "2026-08-01" in text
    assert "archive" in text


def test_reason_surfaces_unplaceable_firings(tmp_path):
    r = FiringReview(scar_id=9, type="landmine", title="t", file="f",
                     count=12, threshold=10, since="2026-08-01T10:00:00",
                     undated=4)
    assert "4 undated" in r.reason()


# --- CLI surface ----------------------------------------------------------

def _run(monkeypatch, tmp_path, store, records, argv):
    from scar import cli
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "firing-log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    monkeypatch.chdir(store.root)
    return cli.main(argv)


def test_status_json_reports_firing_review(monkeypatch, tmp_path, capsys):
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 2\n")
    rc = _run(monkeypatch, tmp_path, store, _firings(store.root, 1, 4),
              ["status", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["firing_review"] == 1
    assert data["firing_review"][0]["id"] == 1
    assert data["firing_review"][0]["count"] == 4
    assert data["firing_review"][0]["threshold"] == 2


def test_status_keeps_date_review_separate_from_count_review(monkeypatch, tmp_path, capsys):
    # Two different obligations discharged two different ways; merging them
    # would leave a consumer unable to tell which one it is looking at.
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 2\n")
    _run(monkeypatch, tmp_path, store, _firings(store.root, 1, 4), ["status", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["review_due"] == []
    assert len(data["firing_review"]) == 1


def test_lint_reports_but_does_not_fail(monkeypatch, tmp_path, capsys):
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 2\n")
    rc = _run(monkeypatch, tmp_path, store, _firings(store.root, 1, 4), ["lint"])
    assert rc == 0
    assert "firing-review" in capsys.readouterr().out


def test_lint_fail_flag_is_opt_in(monkeypatch, tmp_path, capsys):
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 2\n")
    rc = _run(monkeypatch, tmp_path, store, _firings(store.root, 1, 4),
              ["lint", "--fail-firing-review"])
    capsys.readouterr()
    assert rc == 1


def test_lint_fail_flag_is_quiet_when_nothing_crossed(monkeypatch, tmp_path, capsys):
    store, _ = _repo_with_scar(tmp_path, extra="expires:\n  review_after_firings: 9\n")
    rc = _run(monkeypatch, tmp_path, store, _firings(store.root, 1, 2),
              ["lint", "--fail-firing-review"])
    capsys.readouterr()
    assert rc == 0


def test_an_unreadable_log_never_breaks_status(monkeypatch, tmp_path, capsys):
    store, _ = _repo_with_scar(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "firing-log.jsonl").write_text("{not json\n\x00\n")
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    monkeypatch.chdir(store.root)
    from scar import cli
    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["firing_review"] == 0
