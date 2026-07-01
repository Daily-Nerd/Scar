"""Unit tests for the pure survival decision function in replay_shipped.py.

pick_survivor is the LOCKED disambiguation rule for #101 Phase 4: argmax
Jaccard, tie-only ambiguous, NO similarity floor (a floor is a tuned
threshold, forbidden per issue #54). These tests only exercise the pure
function -- no git, no tree-sitter, no corpus.

Run: uv run --extra symbols pytest experiments/anchor-survival/test_replay_shipped.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_shipped import pick_survivor  # noqa: E402


def test_no_candidates_is_orphan():
    assert pick_survivor([]) == "orphan"


def test_single_candidate_survives_regardless_of_score():
    # No similarity floor: even a low score with a single candidate survives.
    assert pick_survivor([("a.py", 0.4)]) == "survived"
    assert pick_survivor([("a.py", 0.0)]) == "survived"


def test_unique_argmax_among_several_survives():
    candidates = [("a.py", 0.2), ("b.py", 0.9), ("c.py", 0.5)]
    assert pick_survivor(candidates) == "survived"


def test_exact_tie_at_max_is_ambiguous():
    candidates = [("a.py", 0.7), ("b.py", 0.7)]
    assert pick_survivor(candidates) == "ambiguous"


def test_tie_at_max_with_lower_third_candidate_is_still_ambiguous():
    candidates = [("a.py", 0.9), ("b.py", 0.9), ("c.py", 0.1)]
    assert pick_survivor(candidates) == "ambiguous"


def test_low_score_unique_max_survives_no_floor_applied():
    # A floor would kill this (0.01 is nearly disjoint); the locked rule has
    # no floor, so a unique argmax always survives.
    candidates = [("a.py", 0.01), ("b.py", 0.0)]
    assert pick_survivor(candidates) == "survived"
