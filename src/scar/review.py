"""Firing-count review triggers (#274).

A scar firing once is a reminder. A scar firing repeatedly is a different
fact: the guarded code is not being protected, it is being stepped around.
`scar stats` already counts firings; this module is the layer that turns a
count into a flag on the scar itself, where a maintainer actually looks.

Scope, deliberately narrow: this reports. It never archives, challenges or
rewrites a scar — ADR-4 keeps lifecycle transitions human.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Per-type defaults, applied when a scar carries no explicit
# `expires.review_after_firings`.
#
# landmine (10): a landmine fires because you are touching coupled code. Ten
#   firings without a revision means the coupling is live and load-bearing —
#   either fix it or write down why it stays.
# fence (15): a fence fires because a file is being edited, and a hot file
#   trips it for reasons that say nothing about the fence itself. Higher bar.
# deadend (0): disabled. A deadend is history. Being reminded of history
#   carries no obligation, so an accumulating count would be pure noise.
DEFAULT_THRESHOLDS = {"landmine": 10, "fence": 15, "deadend": 0}


@dataclass
class FiringReview:
    """One scar whose firing count has crossed its review threshold."""
    scar_id: int
    type: str
    title: str
    file: str                # repo-relative path
    count: int
    threshold: int
    since: str | None        # last-revision ts; None = count is LIFETIME
    undated: int = 0         # firings excluded because the row carried no ts

    def reason(self) -> str:
        window = (f"since its last revision ({self.since[:10]})"
                  if self.since else "over its whole recorded history")
        text = (f"fired {self.count} times {window}, threshold {self.threshold}"
                " — either the guarded code merits the fix, or the scar merits"
                " revision or archive")
        if self.since is None:
            text += " (no revision point found, so this is a lifetime count)"
        if self.undated:
            text += f"; {self.undated} undated firing(s) could not be placed"
        return text


def threshold_for(scar) -> int:
    """The firing threshold in force for one scar. 0 disables escalation.

    An explicit field always wins, including an explicit 0 — that is how an
    author opts a specific landmine out without touching the defaults.
    """
    explicit = getattr(scar, "review_after_firings", None)
    if explicit is not None:
        return explicit
    return DEFAULT_THRESHOLDS.get(scar.type, 0)


def last_revised(repo: Path, rel_paths: list[str]) -> dict[str, str]:
    """repo-relative path -> timestamp of the last commit touching it.

    Timestamps are rendered in LOCAL time with no offset, matching exactly the
    `%Y-%m-%dT%H:%M:%S` shape the firing-log writer produces. This is the whole
    reason for `--date=format-local:` rather than `%cI`: the log writes naive
    local time, so comparing it against an offset-bearing ISO string is a
    string comparison between two different clocks, and it silently shifts the
    window by the UTC offset.

    A path missing from the result has no known revision point (untracked, or
    no git history). Callers MUST treat that as unknown, never as epoch zero:
    a scar whose reset point cannot be found should report a lifetime count
    and say so, not report a since-revision count it did not compute.
    """
    if not rel_paths:
        return {}
    proc = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%x01%cd",
         "--date=format-local:%Y-%m-%dT%H:%M:%S", "--name-only", "--"] + rel_paths,
        capture_output=True, text=True)
    if proc.returncode != 0:
        return {}
    wanted = set(rel_paths)
    out: dict[str, str] = {}
    current = ""
    for line in proc.stdout.splitlines():
        if line.startswith("\x01"):
            current = line[1:].strip()
            continue
        path = line.strip()
        # git log walks newest-first, so the FIRST sighting of a path is its
        # most recent commit. Later (older) sightings must not overwrite it.
        if path and current and path in wanted and path not in out:
            out[path] = current
    return out


def firing_timestamps(records: list[dict], repo_key: str) -> tuple[dict[int, list[str]], dict[int, int]]:
    """(scar id -> firing timestamps, scar id -> undated firing count).

    Counts the SAME rows `scar stats` counts — precheck injection rows, keyed
    on `scar_ids` — so the two surfaces can never disagree about what the word
    "firing" means. Violation rows are not firings and are not counted here.

    Records are tolerated in any shape: this log is written best-effort from a
    fail-open hook and any JSON value can appear on a line (landmine #12).
    """
    dated: dict[int, list[str]] = {}
    undated: dict[int, int] = {}
    for rec in records:
        if not isinstance(rec, dict) or rec.get("repo") != repo_key:
            continue
        sids = rec.get("scar_ids")
        if not isinstance(sids, list):
            continue
        ts = rec.get("ts")
        for sid in sids:
            if not isinstance(sid, int):
                continue
            if isinstance(ts, str) and ts:
                dated.setdefault(sid, []).append(ts)
            else:
                undated[sid] = undated.get(sid, 0) + 1
    return dated, undated


def firing_reviews(store, records: list[dict]) -> list[FiringReview]:
    """Every firing scar whose count has crossed its threshold, worst first.

    Only firing scars are considered: an archived scar does not inject, so
    archiving is how a maintainer clears one of these without editing it.
    """
    repo_key = str(store.root)
    dated, undated = firing_timestamps(records, repo_key)
    if not dated and not undated:
        return []

    scars = [(f, s) for f, s in store.firing() if s.id is not None]
    rels = [str(f.relative_to(store.root)) for f, _s in scars]
    revised = last_revised(store.root, rels)

    out: list[FiringReview] = []
    for (f, scar), rel in zip(scars, rels):
        threshold = threshold_for(scar)
        if threshold <= 0:
            continue
        since = revised.get(rel)
        stamps = dated.get(scar.id, [])
        if since is None:
            # No reset point: report the lifetime count, INCLUDING undated
            # rows — with no cutoff to place them against, they are not
            # ambiguous, they simply count.
            count = len(stamps) + undated.get(scar.id, 0)
            unplaced = 0
        else:
            # Naive-local on both sides, so plain string comparison is a
            # chronological comparison. See last_revised.
            count = sum(1 for t in stamps if t >= since)
            unplaced = undated.get(scar.id, 0)
        if count >= threshold:
            out.append(FiringReview(
                scar_id=scar.id, type=scar.type, title=scar.title, file=rel,
                count=count, threshold=threshold, since=since, undated=unplaced))
    out.sort(key=lambda r: (-(r.count - r.threshold), r.scar_id))
    return out
