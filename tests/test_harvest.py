"""Harvest heuristics over a synthetic git history."""

import subprocess

import pytest

from scar.harvest import (
    harvest, score_candidate, candidate_id, precision_at_n, precision_report,
)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def history(tmp_path):
    git(tmp_path.parent, "init", "-q", "-b", "main", str(tmp_path))
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    # component that will die
    comp = tmp_path / "apps" / "shortlived"
    comp.mkdir(parents=True)
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "feat: add shortlived app")
    # flapping value
    (comp / "deploy.yaml").write_text("replicas: 3\n")
    git(tmp_path, "commit", "-qam", "feat: scale up")
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    git(tmp_path, "commit", "-qam", "fix: revert replicas, instance broke")
    # delete the component
    (comp / "deploy.yaml").unlink()
    comp.rmdir()
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "chore: remove shortlived app")
    return tmp_path


def test_finds_revert_shaped_commits(history):
    result = harvest(history)
    assert any("revert" in c["subject"].lower() for c in result["reverts"])


def test_finds_deleted_components(history):
    result = harvest(history)
    assert any(c["component"] == "apps/shortlived" for c in result["deleted_components"])


def test_finds_value_flapping(history):
    result = harvest(history)
    flaps = result["flapping"]
    assert any(f["key"] == "replicas" and "1 -> 3 -> 1" in f["sequence"] for f in flaps)


def test_clean_repo_yields_empty_sections(tmp_path):
    git(tmp_path.parent, "init", "-q", "-b", "main", str(tmp_path))
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("x")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "feat: initial")
    result = harvest(tmp_path)
    assert result["reverts"] == [] and result["deleted_components"] == []


def test_excludes_scars_dir_from_candidates(tmp_path):
    """Scar #55: harvest must not surface candidates pointing into .scars/.

    Comment-archaeology greps DO-NOT/load-bearing/intentional prose, and a
    repo's own scar bodies are full of exactly that. Without an exclusion every
    promoted scar self-matches and reads as a fresh candidate (same self-ref
    class as #35). .scars/** must produce zero candidates across all detectors.
    """
    git(tmp_path.parent, "init", "-q", "-b", "main", str(tmp_path))
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    # real code comment — SHOULD still be harvested
    (tmp_path / "app.py").write_text(
        "# DO NOT remove this init — load-bearing\nx = 1\n")
    # promoted scar bodies full of trigger prose — must NOT be harvested,
    # at the repo root AND nested (e.g. a fixture's own .scars/ tree).
    body = ("---\ntype: deadend\n---\n"
            "This is intentional. DO NOT remove; load-bearing workaround.\n")
    root_scars = tmp_path / ".scars"
    root_scars.mkdir()
    (root_scars / "0001-x.deadend.md").write_text(body)
    nested_scars = tmp_path / "experiments" / "fixture" / ".scars"
    nested_scars.mkdir(parents=True)
    (nested_scars / "0001-y.fence.md").write_text(body)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "feat: code + scars")

    result = harvest(tmp_path)
    locations = [c["location"] for c in result["comments"]]
    assert any(loc.startswith("app.py") for loc in locations), \
        "real code comment should still be harvested"
    all_paths = (
        locations
        + [c["component"] for c in result["deleted_components"]]
        + [c["file"] for c in result["flapping"]]
    )
    assert not any(".scars" in p.split("/") for p in all_paths), \
        "no candidate may point into any .scars/ tree (self-ref noise, #55)"


# ---------------------------------------------------------------------------
# Ranking / scoring tests
# ---------------------------------------------------------------------------

def test_score_is_deterministic():
    """Same candidate dict → same score across repeated calls."""
    candidate = {"commit": "abc12345", "date": "2024-03-01",
                 "subject": "Revert #123 broken deploy"}
    s1 = score_candidate("revert", candidate)
    s2 = score_candidate("revert", candidate)
    assert s1 == s2


def test_revert_with_pr_ref_outscores_bare_revert():
    """A revert linked to a PR/issue (#123) should score higher than a bare one."""
    base = {"commit": "aaa11111", "date": "2025-01-01", "subject": "Revert broken deploy"}
    linked = {"commit": "bbb22222", "date": "2025-01-01", "subject": "Revert #456 broken deploy"}
    assert score_candidate("revert", linked) > score_candidate("revert", base)


def test_deleted_component_many_files_outscores_few():
    """Deleted component with files_deleted > threshold outscores one below threshold."""
    few = {"component": "apps/small", "died": "2025-01-01",
           "death_commit": "abc", "death_subject": "rm small", "files_deleted": 1}
    many = {"component": "apps/big", "died": "2025-01-01",
            "death_commit": "def", "death_subject": "rm big", "files_deleted": 10}
    assert score_candidate("deleted_component", many) > score_candidate("deleted_component", few)


def test_flapping_more_oscillations_outscores_fewer():
    """Flapping with oscillation_count=3 outscores oscillation_count=1."""
    low = {"file": "deploy.yaml", "key": "replicas", "sequence": "1->3->1",
           "commits": ["a", "b", "c"], "oscillation_count": 1}
    high = {"file": "deploy.yaml", "key": "replicas", "sequence": "1->3->1->3->1->3->1",
            "commits": ["a", "b", "c", "d", "e", "f", "g"], "oscillation_count": 3}
    assert score_candidate("flapping", high) > score_candidate("flapping", low)


@pytest.fixture
def flapping_history(tmp_path):
    """History with 4 oscillation cycles for the same key (1→3→1→3→1→3→1→3→1)."""
    git(tmp_path.parent, "init", "-q", "-b", "main", str(tmp_path))
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    comp = tmp_path / "apps" / "svc"
    comp.mkdir(parents=True)
    (comp / "deploy.yaml").write_text("replicas: 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "feat: initial")
    for _ in range(4):
        (comp / "deploy.yaml").write_text("replicas: 3\n")
        git(tmp_path, "commit", "-qam", "scale up")
        (comp / "deploy.yaml").write_text("replicas: 1\n")
        git(tmp_path, "commit", "-qam", "scale down")
    return tmp_path


def test_flapping_oscillation_count_gt_1(flapping_history):
    """_flapping must count all A→B→A cycles, not just detect the first one."""
    result = harvest(flapping_history)
    flaps = result["flapping"]
    assert len(flaps) >= 1
    rep = next(f for f in flaps if f["key"] == "replicas")
    # 4 up+down pairs = 4 oscillation cycles
    assert rep["oscillation_count"] >= 2, (
        f"Expected oscillation_count >= 2 for 4 cycles, got {rep['oscillation_count']}"
    )


def test_comment_do_not_outscores_generic_xxx():
    """A 'DO NOT delete' comment should outscore a generic XXX comment."""
    generic = {"location": "src/foo.py:10", "text": "XXX fix this later"}
    specific = {"location": "src/bar.py:20", "text": "DO NOT delete — load-bearing init"}
    assert score_candidate("comment", specific) > score_candidate("comment", generic)


def test_comment_base_below_revert_and_deleted_bases():
    """comment base score (no bonus) is below revert base and deleted_component base.

    Raw grep results are the noisiest signal, so even a generic XXX comment
    must rank below a plain revert or a plain deleted component (same 'no bonus' conditions).
    """
    generic_comment = {"location": "src/foo.py:10", "text": "XXX old hack"}
    plain_revert = {"commit": "aaa11111", "date": "2020-01-01", "subject": "Revert something"}
    plain_deleted = {"component": "apps/old", "died": "2020-01-01",
                     "death_commit": "bbb", "death_subject": "rm old", "files_deleted": 1}
    comment_score = score_candidate("comment", generic_comment)
    assert comment_score < score_candidate("revert", plain_revert)
    assert comment_score < score_candidate("deleted_component", plain_deleted)


@pytest.fixture
def multi_history(tmp_path):
    """Richer history: 2 reverts (one with PR ref, one bare) to verify sort order."""
    git(tmp_path.parent, "init", "-q", "-b", "main", str(tmp_path))
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("x")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "feat: initial")
    # bare revert (lower score)
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "Revert broken thing")
    # linked revert (higher score — PR ref)
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "Revert #99 broken deploy")
    return tmp_path


def test_harvest_sections_sorted_by_score_descending(multi_history):
    """Each section in harvest() output with >=2 items must be sorted score descending."""
    result = harvest(multi_history)
    reverts = result["reverts"]
    # Fixture produces 2 reverts — verify they are sorted
    assert len(reverts) >= 2, "Expected at least 2 reverts in multi_history fixture"
    scores = [c["score"] for c in reverts]
    assert scores == sorted(scores, reverse=True), (
        f"Reverts not sorted by score descending: {scores}"
    )


def test_every_candidate_has_score_and_id(history):
    """Every candidate dict returned by harvest() must have score: float and id: str."""
    result = harvest(history)
    for section_name, candidates in result.items():
        for c in candidates:
            assert "score" in c, f"Missing 'score' in {section_name} candidate: {c}"
            assert isinstance(c["score"], float), (
                f"score must be float, got {type(c['score'])} in {section_name}"
            )
            assert "id" in c, f"Missing 'id' in {section_name} candidate: {c}"
            assert isinstance(c["id"], str), (
                f"id must be str, got {type(c['id'])} in {section_name}"
            )
            assert len(c["id"]) == 10, (
                f"id must be 10 hex chars, got len={len(c['id'])} in {section_name}"
            )


def test_stable_candidate_id():
    """Same candidate across two calls → identical id; different candidates → different ids."""
    revert_a = {"commit": "abc12345", "date": "2025-01-01", "subject": "Revert X"}
    revert_b = {"commit": "xyz99999", "date": "2025-02-01", "subject": "Revert Y"}

    id_a1 = candidate_id("revert", revert_a)
    id_a2 = candidate_id("revert", revert_a)
    id_b = candidate_id("revert", revert_b)

    assert id_a1 == id_a2, "Same candidate must produce the same id across calls"
    assert id_a1 != id_b, "Different candidates must produce different ids"

    # Also verify across types — same field content but different signal type → different id
    deleted = {"component": "abc12345", "died": "2025-01-01",
               "death_commit": "a", "death_subject": "rm", "files_deleted": 1}
    assert candidate_id("revert", revert_a) != candidate_id("deleted_component", deleted)


def test_precision_at_n():
    """precision_at_n returns fraction of labeled top-N that are 'keep'; unlabeled excluded.

    Contract enforced by this test:
    - Candidates are taken by position (caller pre-sorts by score desc).
    - Only candidates with an id present in the labels dict are counted.
    - Unlabeled candidates are excluded from BOTH numerator and denominator.
    - If no labeled candidates appear in top-N, returns 0.0.

    Fixture: 5 candidates ranked [A, B, C, D, E].
    Labels: A=keep, B=discard, D=keep (C and E are unlabeled).
    N=3 → top-3 = [A, B, C].  Labeled subset = [A, B].  Kept = [A].
    Expected precision@3 = 1/2 = 0.5.
    """
    ranked = [
        {"id": "A", "score": 5.0},
        {"id": "B", "score": 4.0},
        {"id": "C", "score": 3.0},  # unlabeled
        {"id": "D", "score": 2.0},
        {"id": "E", "score": 1.0},  # unlabeled
    ]
    labels = {"A": "keep", "B": "discard", "D": "keep"}

    assert precision_at_n(ranked, labels, n=3) == 0.5

    # N=5 → top-5 = [A, B, C, D, E]. Labeled = [A, B, D]. Kept = [A, D]. P = 2/3.
    result = precision_at_n(ranked, labels, n=5)
    assert abs(result - 2 / 3) < 1e-9

    # N=1 → top-1 = [A]. Labeled = [A]. Kept = [A]. P = 1.0.
    assert precision_at_n(ranked, labels, n=1) == 1.0

    # All unlabeled in top-N → returns 0.0 (not a division by zero)
    assert precision_at_n(ranked, {}, n=3) == 0.0


def test_precision_report_computes_lift_over_base_rate():
    """precision_report assembles precision@N, the base rate (precision over ALL
    labeled = no-ranking baseline), and lift = precision@N − base_rate.

    Fixture: ranked [A,B,C,D,E]; labels A=keep, B=discard, D=keep (C,E unlabeled).
    base_rate = keeps / labeled over all = {A,D} / {A,B,D} = 2/3.
    @1: top=[A], labeled=[A], P=1.0, lift=1/3, labeled_in_top=1.
    @3: top=[A,B,C], labeled=[A,B], P=1/2, lift=1/2−2/3, labeled_in_top=2.
    """
    ranked = [
        {"id": "A", "score": 5.0},
        {"id": "B", "score": 4.0},
        {"id": "C", "score": 3.0},
        {"id": "D", "score": 2.0},
        {"id": "E", "score": 1.0},
    ]
    labels = {"A": "keep", "B": "discard", "D": "keep"}
    rep = precision_report(ranked, labels, ns=[1, 3])

    assert rep["total"] == 5
    assert rep["labeled"] == 3
    assert abs(rep["base_rate"] - 2 / 3) < 1e-9
    at = {e["n"]: e for e in rep["at"]}
    assert at[1]["precision"] == 1.0
    assert at[1]["labeled_in_top"] == 1
    assert abs(at[1]["lift"] - (1.0 - 2 / 3)) < 1e-9
    assert at[3]["precision"] == 0.5
    assert at[3]["labeled_in_top"] == 2
    assert abs(at[3]["lift"] - (0.5 - 2 / 3)) < 1e-9


def test_precision_report_caps_n_and_dedupes():
    """N values above the candidate count cap to it and dedupe — asking @5/@10/@20
    of a 3-candidate harvest yields a single @3 row, not three identical rows."""
    ranked = [{"id": x, "score": 1.0} for x in ("A", "B", "C")]
    rep = precision_report(ranked, {}, ns=[5, 10, 20])
    assert [e["n"] for e in rep["at"]] == [3]
    assert rep["total"] == 3
    assert rep["labeled"] == 0
    assert rep["base_rate"] == 0.0
