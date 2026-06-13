"""Orphan detection — READ-ONLY module.

A scar is orphan-detected when ALL its anchors fail:
- every path_anchor resolves to no existing tracked file/dir
- every pattern_anchor matches nothing (tracked paths + tracked file contents)

Partial survival (one live anchor of any kind) = NOT orphaned.
Only active + challenged scars are scanned (store.firing()).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .match import _path_anchor_matches, _pattern_anchor_matches
from .model import Scar
from .store import ScarStore

# Caller constants — used by the CLI batch that builds RepoContext from disk.
# The detector itself is content-agnostic; callers honour these when reading files.
MAX_CONTENT_BYTES = 1024 * 1024      # 1 MB — skip oversized / binary files
READ_HEAD_BYTES = 8 * 1024           # first 8 KB for content matching


@dataclass
class RepoContext:
    """Encapsulates the tracked-file universe for one detection run.

    tracked_paths : repo-relative paths returned by ``git ls-files``
    file_contents : repo-relative path → file text (pre-read excerpt for
                    content matching; absent key = content not loaded)
    """
    tracked_paths: list[str]
    file_contents: dict[str, str] = field(default_factory=dict)


@dataclass
class OrphanFinding:
    """A scar whose every anchor is dead."""
    scar_id: int | None
    dead_path_anchors: list[str]      # path anchors that resolved to nothing
    dead_pattern_anchors: list[str]   # pattern anchors that matched nothing


# ---------------------------------------------------------------------------
# RepoContext builder — the one place that touches git + the filesystem
# ---------------------------------------------------------------------------

def build_repo_context(repo: Path) -> RepoContext:
    """Build a RepoContext from a real repo: `git ls-files` for tracked paths,
    and a decoded text excerpt of each (skipping binary / oversize files).

    Files larger than MAX_CONTENT_BYTES are skipped; the rest are read up to
    READ_HEAD_BYTES. Undecodable (binary) files are tracked but their content
    is not loaded, so only their PATH can satisfy a pattern anchor."""
    repo = Path(repo)
    out = subprocess.run(["git", "-C", str(repo), "ls-files"],
                         capture_output=True, text=True).stdout
    tracked = [line for line in out.splitlines() if line]

    contents: dict[str, str] = {}
    for rel in tracked:
        fp = repo / rel
        try:
            if fp.stat().st_size > MAX_CONTENT_BYTES:
                continue
            raw = fp.read_bytes()[:READ_HEAD_BYTES]
            contents[rel] = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # missing, binary, or unreadable — path stays, content skipped
    return RepoContext(tracked_paths=tracked, file_contents=contents)


# ---------------------------------------------------------------------------
# Internal helpers — liveness over a whole tracked set, built on the SHARED
# per-anchor primitives imported from match.py. Detection and injection
# physically share the rule (Issue #33 AC#1).
# ---------------------------------------------------------------------------

def _path_anchor_live(anchor: str, tracked_paths: list[str]) -> bool:
    """True if ANY tracked path satisfies this path anchor."""
    return any(_path_anchor_matches(anchor, p) for p in tracked_paths)


def _pattern_anchor_live(pattern: str, ctx: RepoContext) -> bool:
    """True if the pattern matches any tracked path OR any loaded content excerpt.

    Invalid regex → False (handled inside _pattern_anchor_matches: lint's job).
    Binary / oversized files are skipped (content key absent from ctx).
    """
    for p in ctx.tracked_paths:
        if _pattern_anchor_matches(pattern, p):
            return True
    for content in ctx.file_contents.values():
        if content and _pattern_anchor_matches(pattern, content):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def anchors_all_dead(scar: Scar, ctx: RepoContext) -> bool:
    """Return True when every anchor on *scar* is dead in *ctx*.

    Usable bidirectionally:
    - forward:  orphan detection (active/challenged scar → all dead → orphan)
    - reverse:  revert hint (orphaned scar → not all dead → anchors live again)
    """
    # A scar with NO anchors at all is treated as dead (nothing to hold it alive).
    if not scar.path_anchors and not scar.pattern_anchors:
        return True

    for anchor in scar.path_anchors:
        if _path_anchor_live(anchor, ctx.tracked_paths):
            return False   # at least one live anchor → not all dead

    for pattern in scar.pattern_anchors:
        if _pattern_anchor_live(pattern, ctx):
            return False   # at least one live anchor → not all dead

    return True


def detect_orphans(store: ScarStore, ctx: RepoContext) -> list[OrphanFinding]:
    """Scan active + challenged scars and return those whose every anchor is dead.

    Read-only. Never writes status. Skips unparseable scar files silently.
    """
    findings: list[OrphanFinding] = []

    for _source, scar in store.firing():
        try:
            # Single-source the orphan decision through anchors_all_dead so the
            # zero-anchor policy lives in exactly one place (Issue #33 AC#1 ethos,
            # one level down). The dead-anchor lists below are for REPORTING only.
            if not anchors_all_dead(scar, ctx):
                continue
            dead_paths = [
                a for a in scar.path_anchors
                if not _path_anchor_live(a, ctx.tracked_paths)
            ]
            dead_patterns = [
                p for p in scar.pattern_anchors
                if not _pattern_anchor_live(p, ctx)
            ]
            findings.append(OrphanFinding(
                scar_id=scar.id,
                dead_path_anchors=dead_paths,
                dead_pattern_anchors=dead_patterns,
            ))
        except Exception:
            # store.firing() already skips ParseError; this guards anything unexpected
            continue

    return findings
