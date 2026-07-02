"""Universal authoring trigger (#117) — transcript-free abandonment detection.

``stop_drafter`` (hooks.py) only exists inside Claude Code: it parses the
harness transcript. Every other runtime (Codex, Cursor, opencode, a human at
a terminal) gets scar *reading* via MCP/agent config but nothing ever prompts
a draft. ``draft_check`` closes that gap from git evidence alone — no
transcript required, so it works for anyone who commits with git.

Signals (window = since the last-check marker's mtime, capped at 24h):
  a. commit messages matching ``hooks.REVERT_RE`` (reused, not duplicated)
  b. actual ``git revert`` commits (subject starts "Revert ") and reset
     entries in ``git reflog`` (see _reset_reflog_hits docstring — reflog
     cannot distinguish --hard from --mixed, so any "reset: moving to" entry
     counts; false triggers have the same fp-log escape hatch as everything
     else here)
  c. churn: the same file touched in >=4 of the last 10 commits (unwindowed
     — "last 10 commits" is the whole signal, not "last 10 commits in the
     window")

State (the per-repo last-check + throttle markers) lives in the same
directory as every other hook marker (``hooks._state_dir()`` /
``SCAR_STATE_DIR``). There is no session id here (no transcript, no harness),
so markers are keyed by a hash of the repo's resolved toplevel path instead
of a session id — see ``marker_key``.

Advisory only: every path here degrades silently (returns None / an empty
result) rather than raising. A non-git directory, a git binary that is
missing, a corrupted repo — none of it may crash or fake a signal (mirrors
evidence._git's CompletedProcess-and-check-returncode posture, orphan.py's
GitError distinction between "no signal" and "git itself is broken").
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .hooks import REVERT_RE
from .store import ScarStore

ONE_DAY_SECONDS = 86400
THROTTLE_SECONDS = 3600
CHURN_LOOKBACK = 10
CHURN_THRESHOLD = 4

_RESET_REFLOG_RE = re.compile(r"HEAD@\{(\d+)\}:\s*(reset:\s*moving to .*)$")
_UNIT_SEP = "\x1f"
_RECORD_SEP = "\x1e"  # NOT \x00 — a NUL byte in an argv string is illegal (embedded null)


@dataclass
class DraftCheckResult:
    """One draft-check run's findings. ``triggered`` is the OR of all four
    signal lists being non-empty — callers should not need to re-derive it."""
    revert_messages: list[str] = field(default_factory=list)
    revert_commits: list[str] = field(default_factory=list)
    reset_entries: list[str] = field(default_factory=list)
    churn_files: list[str] = field(default_factory=list)
    window_start: float = 0.0

    @property
    def triggered(self) -> bool:
        return bool(self.revert_messages or self.revert_commits
                    or self.reset_entries or self.churn_files)

    def signal_counts(self) -> dict[str, int]:
        return {"revert_language": len(self.revert_messages),
                "revert_commits": len(self.revert_commits),
                "reset_hard": len(self.reset_entries),
                "churn": len(self.churn_files)}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def repo_toplevel(start: Path) -> Path | None:
    """The repo's working-tree root, or None when *start* is not inside a git
    repo (or git itself is unusable there) — the one non-git-degrades-silently
    check every other function here assumes has already passed."""
    proc = _git(start, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    if not out:
        return None
    return Path(out)


def marker_key(repo: Path) -> str:
    """Stable per-repo id for marker filenames. The firing log (hooks.py
    #106) keys by a ``repo`` field inside each JSON line, not by filename —
    there's no such record here (draft-check has no log to key), so the
    marker file itself is keyed by hashing the resolved repo path."""
    return hashlib.sha1(str(repo.resolve()).encode("utf-8")).hexdigest()


def lastcheck_marker(state_dir: Path, repo: Path) -> Path:
    return state_dir / f"draftcheck-{marker_key(repo)}-lastcheck"


def throttle_marker(state_dir: Path, repo: Path) -> Path:
    return state_dir / f"draftcheck-{marker_key(repo)}-throttle"


def is_throttled(state_dir: Path, repo: Path) -> bool:
    """True iff the throttle marker exists and is under an hour old. Only
    meaningful for ``--from-hook`` callers — a direct `scar draft-check`
    invocation is never throttled."""
    marker = throttle_marker(state_dir, repo)
    try:
        mtime = marker.stat().st_mtime
    except OSError:
        return False
    return (time.time() - mtime) < THROTTLE_SECONDS


def touch_throttle(state_dir: Path, repo: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    throttle_marker(state_dir, repo).touch()


def _window_start(state_dir: Path, repo: Path) -> float:
    """Since the last-check marker's mtime, capped at 24h — a marker that is
    a week stale must NOT reopen a week of history, and a repo checked for
    the first time has no marker at all (same 24h floor)."""
    floor = time.time() - ONE_DAY_SECONDS
    marker = lastcheck_marker(state_dir, repo)
    try:
        mtime = marker.stat().st_mtime
    except OSError:
        return floor
    return max(mtime, floor)


def _touch_lastcheck(state_dir: Path, repo: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    lastcheck_marker(state_dir, repo).touch()


def _commits_since(repo: Path, since: float) -> list[tuple[str, str]]:
    """(sha, subject) pairs committed at/after *since*. Empty (never raises)
    on any git failure — a broken log is a "no signal" state, not a crash."""
    proc = _git(repo, "log", f"--since=@{int(since)}", f"--format=%H{_UNIT_SEP}%s")
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        sha, _, subject = line.partition(_UNIT_SEP)
        out.append((sha, subject))
    return out


def _revert_language_hits(commits: list[tuple[str, str]]) -> list[str]:
    return [subject for _, subject in commits if REVERT_RE.search(subject)]


def _revert_commit_hits(commits: list[tuple[str, str]]) -> list[str]:
    return [subject for _, subject in commits if subject.startswith("Revert ")]


def _reset_reflog_hits(repo: Path, since: float) -> list[str]:
    """Reflog entries within the window whose message is a reset ("reset:
    moving to <target>"). git's reflog message does NOT distinguish --hard
    from --mixed/--soft — the subject line is identical for all three — so
    this counts every reset, not just --hard ones. That is a superset of the
    issue's "reset --hard" wording, never a narrower one: a real --hard reset
    always matches, and any extra (rare, interactive) --mixed/--soft match is
    exactly the kind of false trigger the fp-log escape hatch exists for.

    ``git reflog`` lists newest-first, so this stops at the first entry older
    than the window instead of scanning the whole reflog."""
    proc = _git(repo, "reflog", "--date=unix")
    if proc.returncode != 0:
        return []
    hits = []
    for line in proc.stdout.splitlines():
        m = _RESET_REFLOG_RE.search(line)
        if not m:
            continue
        ts = int(m.group(1))
        if ts < since:
            break
        hits.append(m.group(2))
    return hits


def _churn_files(repo: Path) -> list[str]:
    """Paths touched in >=4 of the last 10 commits, regardless of window —
    churn is a shape-of-history signal, not a recency one."""
    proc = _git(repo, "log", f"-{CHURN_LOOKBACK}", "--name-only",
                f"--pretty=format:{_RECORD_SEP}%H")
    if proc.returncode != 0:
        return []
    counts: dict[str, int] = {}
    for block in proc.stdout.split(_RECORD_SEP):
        lines = block.splitlines()
        if not lines:
            continue
        for path in set(line for line in lines[1:] if line.strip()):
            counts[path] = counts.get(path, 0) + 1
    return sorted(path for path, n in counts.items() if n >= CHURN_THRESHOLD)


def analyze(state_dir: Path, repo: Path) -> DraftCheckResult | None:
    """Run every signal and update the last-check marker. Returns None when
    *repo* is not inside a git repo (or git is unusable there) — callers must
    treat that as "say nothing", per the non-git-degrades-silently contract.

    The last-check marker is touched on every successful run (triggered or
    not) — it marks "checked up to now", not "found something"."""
    top = repo_toplevel(repo)
    if top is None:
        return None
    since = _window_start(state_dir, top)
    commits = _commits_since(top, since)
    result = DraftCheckResult(
        revert_messages=_revert_language_hits(commits),
        revert_commits=_revert_commit_hits(commits),
        reset_entries=_reset_reflog_hits(top, since),
        churn_files=_churn_files(top),
        window_start=since,
    )
    _touch_lastcheck(state_dir, top)
    return result


def contract_text(store: ScarStore, result: DraftCheckResult) -> str:
    """Mirrors ``hooks.stop_drafter``'s contract text (hooks.py ~194-225):
    same two-branch instruction (write a candidate, or log a false
    positive), same <=15 line cap, same template/candidates paths. Adapted:
    no transcript signals (git evidence instead) and the fp-log branch tells
    the agent to tag its line ``draft-check`` so drafter-precision data keeps
    the two trigger sources (transcript vs git) separable."""
    counts = result.signal_counts()
    signal_desc = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
    candidates = store.scars_dir / "candidates"
    return (
        "SCAR draft-check: recent git history shows abandonment signals "
        f"({signal_desc}). Review the recent history. "
        f"(1) If an approach was genuinely tried and abandoned, write a short "
        f"candidate scar (<=15 lines) to {candidates}/<slug>.md — COPY the "
        f"format from {store.scars_dir}/template.md (YAML frontmatter "
        "mandatory, status: candidate); it stays a candidate until a human "
        "reviews it. (2) If nothing was actually abandoned (false trigger), "
        f"append one line — date + one-phrase reason — to "
        f"{candidates}/fp-log.txt, tagged `draft-check` (e.g. '2026-07-01 "
        "draft-check: <reason>') so drafter-precision data stays separated "
        "by trigger source. Do exactly one of the two; do not ask the user."
    )
