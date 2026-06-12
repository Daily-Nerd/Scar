"""Harvest: mine git history for negative-knowledge candidates.

Port of the gate-0.1 prototype (experiments/harvest/), returning structured
data instead of printing. Six heuristics; each returns CANDIDATES that a
human curates — raw precision measured at ~13% on real history, so the CLI
layer must always present these as "needs confirmation", never as scars.

NB (scar 0001): no \\b in git grep patterns, no speculative extension globs.
"""

from __future__ import annotations

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
        for i in range(len(values) - 2):
            if values[i] == values[i + 2] and values[i] != values[i + 1]:
                flaps.append({"file": fname, "key": key,
                              "sequence": f"{values[i]} -> {values[i+1]} -> {values[i+2]}",
                              "commits": [seq[i][1], seq[i+1][1], seq[i+2][1]]})
                break
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


def harvest(repo: Path) -> dict[str, list[dict]]:
    repo = Path(repo)
    return {
        "reverts": _reverts(repo),
        "deleted_components": _deleted_components(repo),
        "flapping": _flapping(repo),
        "comments": _comment_archaeology(repo),
    }
