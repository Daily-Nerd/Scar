"""Path/symbol re-anchoring — orphan recovery v1 (#111).

READ-ONLY tracer: given a dead anchor, propose where the protected content
likely moved to. Off the hot path by construction — nothing here is imported
by match.py or hooks.py (same guarantee as renames.py / orphan.py — see the
structural test in test_match.py).

### Path tracing
1. killing-commit resolution: `git log --diff-filter=D -1 -- <old>` finds the
   commit that deleted <old>. A same-commit move (delete + add of the twin
   content in one commit) needs nothing more — `git show` on that one commit
   gives BOTH the dead file's removed lines and every other touched file's
   added lines in a single diff.
2. Rank candidates by signature-line overlap (strip whitespace, drop blank /
   short lines — see `_signature_lines`). Two-tier confidence from the raw
   overlap ratio: a measured signal, not a calibrated probability (#95
   posture — confidence stays a static, non-decaying label here too).
3. Bounded pickaxe fallback (`git log -S<line> -n 20`) ONLY when the
   killing-commit ranking found nothing above the low threshold — covers a
   later-commit move (content deleted here, re-added somewhere else in a
   different, later commit).

Every failure mode (non-git, shallow, git error) degrades to "no proposals",
never raises — same contract as orphan.py / renames.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .evidence import _git, _is_shallow

# Signature-line filter: blank lines and lines under this length carry too
# little content to mean anything as a moved-content signal (a bare brace, a
# single short import) — they would flood every candidate file with bogus
# overlap. 10 chars is the same conservative floor used elsewhere in this
# codebase's line-based heuristics; no line this short survives as a unique
# fingerprint of "this content moved here".
_MIN_SIGNATURE_LEN = 10

# Path-tracing confidence tiers, from the measured signature-line overlap
# ratio (|dead ∩ candidate| / |dead|). HIGH: the large majority of the dead
# file's substantial lines are found intact in the candidate — a same-commit
# `git mv`-shaped move typically lands at ~0.9-1.0, so 0.6 leaves headroom for
# minor reformatting while still requiring most of the content to be present.
# LOW: partial but non-trivial overlap (content merged/refactored on the way,
# or the pickaxe fallback's weaker single-line signal) — surfaced for human
# review, never auto-applied. Below LOW: noise, not surfaced at all. Both
# numbers are heuristic, picked for this v1, not fit to labelled data (#54/#95
# discipline: don't pretend a threshold is calibrated at n=0).
PATH_HIGH_THRESHOLD = 0.6
PATH_LOW_THRESHOLD = 0.2

# Bounded pickaxe fallback: cap the number of commits inspected for the
# distinctive-line search — never an unbounded history walk (#111 AC).
_PICKAXE_LIMIT = 20
_PICKAXE_LINE_CAP = 200  # keep the -S argument sane for pathological lines


@dataclass
class ReanchorProposal:
    """One candidate replacement for a dead anchor."""
    scar_id: int | None
    anchor_kind: str          # "path" | "symbol"
    dead_anchor: str
    proposed_anchor: str
    confidence: str           # "high" | "low"
    signal: float              # raw overlap/jaccard ratio behind the tier
    evidence: str              # human-readable trace, e.g. "killing-commit <sha7>: overlap 0.83"


def _signature_lines(text: str) -> frozenset[str]:
    return frozenset(
        s for s in (ln.strip() for ln in text.splitlines())
        if len(s) >= _MIN_SIGNATURE_LEN)


def _tier(ratio: float) -> str | None:
    if ratio >= PATH_HIGH_THRESHOLD:
        return "high"
    if ratio >= PATH_LOW_THRESHOLD:
        return "low"
    return None


def _overlap_ratio(dead: frozenset[str], candidate: frozenset[str]) -> float:
    if not dead:
        return 0.0
    return len(dead & candidate) / len(dead)


def _killing_commit(repo: Path, old_path: str) -> str | None:
    """The most recent commit that deleted *old_path*, or None (never
    deleted / not a git repo / git failed)."""
    try:
        proc = _git(repo, "log", "--diff-filter=D", "-1", "--format=%H", "--", old_path)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def _commit_diff(repo: Path, sha: str) -> str | None:
    """The whole commit's unified diff, one call. `--no-renames` forces a
    clean delete+add pair for a moved file instead of git's own rename
    heuristic collapsing it — we want to do the overlap ranking ourselves."""
    try:
        proc = _git(repo, "show", "--no-renames", "--format=", sha)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_diff(diff_text: str, old_path: str) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    """One pass over a unified diff: the removed-line signature for
    *old_path* (what died) and, for every OTHER file touched in the same
    commit, the added-line signature (candidates for where it went)."""
    removed: dict[str, list[str]] = {}
    added: dict[str, list[str]] = {}
    cur_old: str | None = None
    cur_new: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            path = line[4:]
            cur_old = None if path == "/dev/null" else path[2:]  # strip 'a/'
            continue
        if line.startswith("+++ "):
            path = line[4:]
            cur_new = None if path == "/dev/null" else path[2:]  # strip 'b/'
            continue
        if line[:1] == "+":
            if cur_new is not None and cur_new != old_path:
                added.setdefault(cur_new, []).append(line[1:])
            continue
        if line[:1] == "-":
            if cur_old is not None and cur_old == old_path:
                removed.setdefault(cur_old, []).append(line[1:])
            continue
    dead = _signature_lines("\n".join(removed.get(old_path, [])))
    added_sig = {p: _signature_lines("\n".join(lines)) for p, lines in added.items()}
    return dead, added_sig


def _rank(dead: frozenset[str], candidates: dict[str, frozenset[str]]) -> list[tuple[str, float]]:
    scored = []
    for path, sig in candidates.items():
        ratio = _overlap_ratio(dead, sig)
        if _tier(ratio) is not None:
            scored.append((path, ratio))
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored


def _pickaxe_candidates(repo: Path, line: str, old_path: str, tracked: set[str]) -> list[str]:
    """Bounded fallback: commits (at most `_PICKAXE_LIMIT`) whose diff added
    or removed an occurrence of *line* — the dead file's most distinctive
    surviving signature line. Returns currently-tracked file names only,
    excluding *old_path* itself."""
    try:
        proc = _git(repo, "log", f"-S{line}", "-n", str(_PICKAXE_LIMIT),
                    "--format=", "--name-only", "--diff-filter=AM")
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    out: list[str] = []
    for ln in proc.stdout.splitlines():
        ln = ln.strip()
        if ln and ln != old_path and ln in tracked and ln not in out:
            out.append(ln)
    return out


def trace_dead_path(repo: Path, old_path: str,
                    tracked: set[str]) -> list[tuple[str, float, str]]:
    """Candidates for where *old_path*'s content moved: a list of
    (proposed_path, overlap_ratio, evidence_text), ranked best-first. Empty
    when the file was never deleted, was deleted with nothing similar found,
    or the repo can't be inspected (non-git, shallow, git failure) — never
    raises."""
    repo = Path(repo)
    try:
        if _is_shallow(repo):
            return []
    except OSError:
        return []

    sha = _killing_commit(repo, old_path)
    if sha is None:
        return []
    diff = _commit_diff(repo, sha)
    if diff is None:
        return []

    dead, added_by_file = _parse_diff(diff, old_path)
    same_commit = {p: sig for p, sig in added_by_file.items() if p in tracked}
    ranked = _rank(dead, same_commit)
    if ranked:
        return [(p, ratio, f"killing-commit {sha[:7]}: overlap {ratio:.2f}")
                for p, ratio in ranked]

    # Killing commit alone found nothing — bounded pickaxe fallback for a
    # later-commit move.
    if not dead:
        return []
    line = max(dead, key=lambda s: (len(s), s))[:_PICKAXE_LINE_CAP]
    results: list[tuple[str, float, str]] = []
    for path in _pickaxe_candidates(repo, line, old_path, tracked):
        try:
            text = (repo / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        ratio = _overlap_ratio(dead, _signature_lines(text))
        if _tier(ratio) is None:
            continue
        results.append((path, ratio, f"pickaxe {sha[:7]}: distinctive-line match, overlap {ratio:.2f}"))
    results.sort(key=lambda t: (-t[1], t[0]))
    return results


def propose_path_reanchors(repo: Path, scar_id: int | None, dead_anchor: str,
                           tracked: set[str]) -> list[ReanchorProposal]:
    """`trace_dead_path` results wrapped into `ReanchorProposal`s for one
    scar's one dead path anchor."""
    candidates = trace_dead_path(repo, dead_anchor, tracked)
    return [
        ReanchorProposal(scar_id=scar_id, anchor_kind="path", dead_anchor=dead_anchor,
                         proposed_anchor=path, confidence=_tier(ratio),
                         signal=round(ratio, 4), evidence=evidence)
        for path, ratio, evidence in candidates
    ]
