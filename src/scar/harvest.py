"""Harvest: mine git history for negative-knowledge candidates.

Port of the gate-0.1 prototype (experiments/harvest/), returning structured
data instead of printing. Six heuristics; each returns CANDIDATES that a
human curates — raw precision measured at ~13% on real history, so the CLI
layer must always present these as "needs confirmation", never as scars.

NB (scar 0001): no \\b in git grep patterns, no speculative extension globs.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import subprocess
from collections import defaultdict
from pathlib import Path

REVERT_RE = re.compile(
    r"revert|rollback|roll back|downgrade|back to|undo|set back|"
    r"retire|disable again", re.IGNORECASE)
TRACKED_KEYS = ("image", "replicas", "tag", "version", "cpu", "memory")
COMMENT_RE = (r"DO NOT|DON'T|do not (remove|change|touch)|HACK|XXX|"
              r"load.?bearing|intentional|must (stay|remain)|workaround")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout


def _commits(repo: Path):
    out = _git(repo, "log", "--format=%H\x01%ad\x01%s", "--date=short")
    for line in out.splitlines():
        h, date, subj = line.split("\x01", 2)
        yield h, date, subj


def _reverts(repo: Path) -> list[dict]:
    return [{"commit": h[:8], "date": d, "subject": s}
            for h, d, s in _commits(repo) if REVERT_RE.search(s)]


def _deleted_components(repo: Path) -> list[dict]:
    out = _git(repo, "log", "--diff-filter=D", "--name-only",
               "--format=\x02%h\x01%ad\x01%s", "--date=short")
    comp_del: dict[str, list] = defaultdict(list)
    commit: list[str] = []
    for line in out.splitlines():
        if line.startswith("\x02"):
            commit = line[1:].split("\x01")
        elif line and "/" in line:
            comp_del["/".join(line.split("/")[:2])].append(commit)
    results = []
    for comp, dels in comp_del.items():
        if (repo / comp).exists():
            continue
        last = dels[0]
        results.append({"component": comp, "died": last[1],
                        "death_commit": last[0], "death_subject": last[2],
                        "files_deleted": len(dels)})
    return sorted(results, key=lambda r: r["died"], reverse=True)


def _flapping(repo: Path) -> list[dict]:
    key_re = re.compile(r"^[+-]\s*(" + "|".join(TRACKED_KEYS) + r"):\s*(.+)$")
    out = _git(repo, "log", "-p", "--reverse", "--format=\x02%h\x01%ad\x01%s",
               "--date=short")
    history: dict[tuple, list] = defaultdict(list)
    commit: list[str] = []
    fname = ""
    for line in out.splitlines():
        if line.startswith("\x02"):
            commit = line[1:].split("\x01")
        elif line.startswith("+++ b/"):
            fname = line[6:]
        elif line.startswith("+") and fname:
            m = key_re.match(line)
            if m:
                history[(fname, m.group(1))].append(
                    (m.group(2).strip(), commit[0], commit[1]))
    flaps = []
    for (fname, key), seq in history.items():
        values = [v for v, _, _ in seq]
        # Count all A→B→A cycles (non-overlapping scan)
        first_i = None
        osc_count = 0
        all_commits: list[str] = []
        for i in range(len(values) - 2):
            if values[i] == values[i + 2] and values[i] != values[i + 1]:
                if first_i is None:
                    first_i = i
                    all_commits = [seq[i][1], seq[i + 1][1], seq[i + 2][1]]
                osc_count += 1
        if first_i is not None:
            fi = first_i
            flaps.append({
                "file": fname,
                "key": key,
                "sequence": (
                    f"{values[fi]} -> {values[fi+1]} -> {values[fi+2]}"
                ),
                "commits": all_commits,
                "oscillation_count": osc_count,
            })
    return flaps


def _comment_archaeology(repo: Path) -> list[dict]:
    out = _git(repo, "grep", "-nIE", COMMENT_RE)  # -I skips binaries; no \b (scar 0001)
    hits = []
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3:
            hits.append({"location": f"{parts[0]}:{parts[1]}",
                         "text": parts[2].strip()[:120]})
    return hits


# ---------------------------------------------------------------------------
# Ranking layer — v1 calibration priors (unvalidated until labels exist)
# ---------------------------------------------------------------------------
# Weight constants: each constant is a "unit of confidence" in the heuristic.
# Calibration note: all weights are priors based on reasoning, not empirical
# labels. They should be tuned once a labels.jsonl set reaches ~50 entries.

_PR_REF_RE = re.compile(r"#\d+")

# High specificity patterns in comments — these strongly suggest load-bearing intent
_COMMENT_HIGH_RE = re.compile(
    r"DO NOT|DON'T|do not (remove|change|touch)|load.?bearing|must (stay|remain)",
    re.IGNORECASE,
)

_REVERT_BASE = 3.0        # reverts signal something failed — reasonably high signal
_REVERT_PR_BONUS = 1.5    # linked PR/issue ref → traceable, more trustworthy
_REVERT_RECENCY_MAX = 1.0 # recent reverts are more likely still relevant

_DELETED_BASE = 2.5       # deleted component may hide intentional retirement
_DELETED_FILES_THRESHOLD = 3   # "many" files deleted → more impactful component
_DELETED_FILES_BONUS = 1.0     # bonus for exceeding threshold

_FLAPPING_BASE = 2.0      # config oscillation is meaningful but lower base (can be noise)
_FLAPPING_OSC_BONUS = 0.5 # per oscillation cycle beyond 1 — more cycles = more signal

_COMMENT_BASE = 1.0       # raw grep is noisiest source — lowest base across all types
_COMMENT_HIGH_BONUS = 1.5 # high-specificity markers (DO NOT, load-bearing) lift the score


def _now_months() -> int:
    """Current year*12+month from the real clock. Isolated so callers can inject
    a fixed value for deterministic tests (never hardcode 'now' in the formula —
    a frozen constant silently drifts wrong with every passing month)."""
    today = datetime.date.today()
    return today.year * 12 + today.month


def _recency_term(date_str: str, max_bonus: float = 1.0,
                  now_months: int | None = None) -> float:
    """Return a bonus [0, max_bonus] based on how recent a date string (YYYY-MM-DD) is.

    Uses simple year/month arithmetic — no external deps. A date within the
    last 12 months gets full bonus; 5+ years old gets 0. Linear interpolation.
    `now_months` defaults to the real clock; pass a fixed value in tests.
    """
    if now_months is None:
        now_months = _now_months()
    try:
        parts = date_str.split("-")
        year, month = int(parts[0]), int(parts[1])
        candidate_months = year * 12 + month
        age_months = now_months - candidate_months
        if age_months <= 12:
            return max_bonus
        if age_months >= 60:
            return 0.0
        # Linear decay from 12→60 months
        return max_bonus * (60 - age_months) / (60 - 12)
    except (IndexError, ValueError):
        return 0.0


def score_candidate(signal_type: str, candidate: dict,
                    now_months: int | None = None) -> float:
    """Return a heuristic weighted score for a candidate dict.

    The score is deterministic for a fixed `now_months`: same inputs → same output.
    `now_months` defaults to the real clock; pass a fixed value in tests so
    recency-sensitive scores don't drift with the calendar.
    Scores are NOT comparable across signal types by design in v1
    (the base constants intentionally rank comment < flapping < deleted < revert).
    """
    if signal_type == "revert":
        score = _REVERT_BASE
        if _PR_REF_RE.search(candidate.get("subject", "")):
            score += _REVERT_PR_BONUS
        score += _recency_term(candidate.get("date", ""), _REVERT_RECENCY_MAX, now_months)
        return score

    if signal_type == "deleted_component":
        score = _DELETED_BASE
        if candidate.get("files_deleted", 0) > _DELETED_FILES_THRESHOLD:
            score += _DELETED_FILES_BONUS
        score += _recency_term(candidate.get("died", ""), _REVERT_RECENCY_MAX, now_months)
        return score

    if signal_type == "flapping":
        score = _FLAPPING_BASE
        # oscillation_count is the number of A→B→A cycles found (>= 1 when present)
        osc = candidate.get("oscillation_count", 1)
        score += _FLAPPING_OSC_BONUS * (osc - 1)  # first cycle already in base
        return score

    if signal_type == "comment":
        score = _COMMENT_BASE
        if _COMMENT_HIGH_RE.search(candidate.get("text", "")):
            score += _COMMENT_HIGH_BONUS
        return score

    # Unknown type — return 0 so unknown candidates sort last
    return 0.0


def candidate_id(signal_type: str, candidate: dict) -> str:
    """Return a stable short hash id for a candidate.

    Stability guarantee: same signal_type + same identifying fields (NOT score,
    NOT id itself) across runs → same id. Two different candidates → different ids.

    Identifying fields per type:
    - revert: commit
    - deleted_component: component
    - flapping: file + key
    - comment: location + text[:40]
    """
    if signal_type == "revert":
        key = f"revert:{candidate.get('commit', '')}"
    elif signal_type == "deleted_component":
        key = f"deleted_component:{candidate.get('component', '')}"
    elif signal_type == "flapping":
        key = f"flapping:{candidate.get('file', '')}:{candidate.get('key', '')}"
    elif signal_type == "comment":
        # text[:40] avoids instability from trailing whitespace / truncation
        key = f"comment:{candidate.get('location', '')}:{candidate.get('text', '')[:40]}"
    else:
        key = f"{signal_type}:{repr(sorted(candidate.items()))}"
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def precision_at_n(
    ranked_candidates: list[dict],
    labels: dict[str, str],
    n: int,
) -> float:
    """Return precision@N: fraction of top-N labeled candidates that are 'keep'.

    Contract:
    - Take the first N candidates from ranked_candidates (by position — caller
      must pass them pre-sorted by score descending).
    - Among those N, consider only candidates whose id appears in labels.
    - Of the labeled subset, return fraction where label == 'keep'.
    - Unlabeled candidates are EXCLUDED from both numerator and denominator.
    - If no labeled candidates exist in top-N, return 0.0 (not NaN).
    """
    top = ranked_candidates[:n]
    labeled_keep = 0
    labeled_total = 0
    for c in top:
        cid = c.get("id", "")
        if cid in labels:
            labeled_total += 1
            if labels[cid] == "keep":
                labeled_keep += 1
    if labeled_total == 0:
        return 0.0
    return labeled_keep / labeled_total


def precision_report(
    ranked_candidates: list[dict],
    labels: dict[str, str],
    ns: list[int],
) -> dict:
    """Assemble a precision@N report against a labeled set (pure, for the CLI).

    Returns:
      total      — number of ranked candidates
      labeled    — how many of them have a label
      base_rate  — precision over ALL labeled, ignoring rank (the no-ranking
                   baseline = precision_at_n at N=total)
      at         — one entry per requested N (capped at total, deduped, ascending):
                   {n, precision, lift (= precision − base_rate), labeled_in_top}

    Caller must pass ranked_candidates pre-sorted by score descending.
    """
    total = len(ranked_candidates)
    labeled = sum(1 for c in ranked_candidates if c.get("id", "") in labels)
    base_rate = precision_at_n(ranked_candidates, labels, total)

    capped_ns = sorted({min(n, total) for n in ns if n > 0})
    at = []
    for n in capped_ns:
        labeled_in_top = sum(
            1 for c in ranked_candidates[:n] if c.get("id", "") in labels)
        precision = precision_at_n(ranked_candidates, labels, n)
        at.append({
            "n": n,
            "precision": precision,
            "lift": precision - base_rate,
            "labeled_in_top": labeled_in_top,
        })

    return {"total": total, "labeled": labeled, "base_rate": base_rate, "at": at}


# ---------------------------------------------------------------------------
# harvest() — annotates and ranks each section
# ---------------------------------------------------------------------------

_SECTION_TYPES = {
    "reverts": "revert",
    "deleted_components": "deleted_component",
    "flapping": "flapping",
    "comments": "comment",
}


def _annotate_and_sort(candidates: list[dict], signal_type: str,
                       now_months: int | None = None) -> list[dict]:
    """Add 'score' and 'id' fields to each candidate, then sort by score desc."""
    for c in candidates:
        c["id"] = candidate_id(signal_type, c)
        c["score"] = score_candidate(signal_type, c, now_months)
    return sorted(candidates, key=lambda c: c["score"], reverse=True)


def harvest(repo: Path) -> dict[str, list[dict]]:
    repo = Path(repo)
    # One 'now' for the whole run so recency ranking is internally consistent.
    now_months = _now_months()
    raw = {
        "reverts": _reverts(repo),
        "deleted_components": _deleted_components(repo),
        "flapping": _flapping(repo),
        "comments": _comment_archaeology(repo),
    }
    return {
        section: _annotate_and_sort(candidates, _SECTION_TYPES[section], now_months)
        for section, candidates in raw.items()
    }
