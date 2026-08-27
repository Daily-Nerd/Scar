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

from . import symbols
from .evidence import _commit_shas, _git, _is_shallow, _reachable
from .match import _path_anchor_matches, _pattern_anchor_matches
from .model import Scar
from .renames import RenameResolver
from .store import ScarStore

# Caller constants — used by the CLI batch that builds RepoContext from disk.
# The detector itself is content-agnostic; callers honour these when reading files.
# Files under the size cap load in FULL (#156): the old 8KB read-head made any
# pattern whose only match sat past byte 8192 report dead — false partial-rot.
# Matching itself still runs through the shared primitive's MAX_ANCHOR_SCAN
# (64 KiB) bound, which keeps the ReDoS surface capped (see landmine #11).
MAX_CONTENT_BYTES = 1024 * 1024      # 1 MB — skip oversized / binary files


class GitError(RuntimeError):
    """A git invocation failed (e.g. the path is not a git repository).

    Surfaced instead of returning an empty tracked set: an empty set makes every
    scar's anchors look dead, flipping the whole repo to 'orphaned' and tripping
    `lint --fail-orphans` as a false CI gate (landmine #1 — silent-empty as truth)."""


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
    renamed: dict[str, str] = field(default_factory=dict)  # dead path anchor -> git rename target (#109)


@dataclass
class SymbolDriftFinding:
    """A symbol-anchored scar whose symbol resolves but drifted since its
    evidence commit. Advisory only; graded similarity, never a transition."""
    scar_id: int | None
    symbol: str
    sha: str
    similarity: float


@dataclass
class PartialRotFinding:
    """A firing scar with ≥1 dead anchor but ≥1 live anchor (#35).

    Distinct from an orphan: the scar still protects something, so it keeps
    firing — but part of its protection has rotted and no surface showed it.
    Advisory only; never drives a status transition (fix is re-anchoring).
    """
    scar_id: int | None
    dead_path_anchors: list[str]      # path anchors that resolved to nothing
    dead_pattern_anchors: list[str]   # pattern anchors that matched nothing
    renamed: dict[str, str] = field(default_factory=dict)  # dead path anchor -> git rename target (#109)
    dead_pattern_branches: list[str] = field(default_factory=list)  # dead top-level alternation branches (#213)


# ---------------------------------------------------------------------------
# RepoContext builder — the one place that touches git + the filesystem
# ---------------------------------------------------------------------------

def build_repo_context(repo: Path) -> RepoContext:
    """Build a RepoContext from a real repo: `git ls-files` for tracked paths,
    and a decoded text excerpt of each (skipping binary / oversize files).

    Files larger than MAX_CONTENT_BYTES are skipped; the rest are read in
    full (#156). Undecodable (binary) files are tracked but their content
    is not loaded, so only their PATH can satisfy a pattern anchor."""
    repo = Path(repo)
    proc = subprocess.run(["git", "-C", str(repo), "ls-files"],
                          capture_output=True)
    if proc.returncode != 0:
        # A valid repo (even one with zero tracked files) returns rc 0 + empty
        # output; rc != 0 means git itself failed (not a repo / bad worktree).
        # Distinguishing the two is the whole point — an empty tracked set here
        # would orphan every scar (landmine #1).
        raise GitError(
            f"git ls-files failed in {repo}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    out = proc.stdout.decode("utf-8", "replace")
    tracked = [line for line in out.splitlines() if line]

    contents: dict[str, str] = {}
    for rel in tracked:
        fp = repo / rel
        try:
            if fp.stat().st_size > MAX_CONTENT_BYTES:
                continue
            raw = fp.read_bytes()
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


def _pattern_anchor_live(pattern: str, ctx: RepoContext,
                         exclude_path: str | None = None) -> bool:
    """True if the pattern matches any tracked path OR any loaded content excerpt.

    Invalid regex → False (handled inside _pattern_anchor_matches: lint's job).
    Binary / oversized files are skipped (content key absent from ctx).

    *exclude_path* (repo-relative) drops one file — the scar's own .scars/ body —
    from BOTH the path and the content check, so a scar cannot keep itself alive
    by quoting, in its own prose, the very pattern it warns about (#35 self-ref).
    """
    for p in ctx.tracked_paths:
        if p == exclude_path:
            continue
        if _pattern_anchor_matches(pattern, p):
            return True
    for rel, content in ctx.file_contents.items():
        if rel == exclude_path:
            continue
        if content and _pattern_anchor_matches(pattern, content):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def anchors_all_dead(scar: Scar, ctx: RepoContext,
                     self_path: str | None = None) -> bool:
    """Return True when every anchor on *scar* is dead in *ctx*.

    Usable bidirectionally:
    - forward:  orphan detection (active/challenged scar → all dead → orphan)
    - reverse:  revert hint (orphaned scar → not all dead → anchors live again)

    *self_path* (repo-relative path of the scar's own .scars/ file) is excluded
    from pattern-content liveness so a scar can't self-reference itself alive (#35).
    """
    # Command anchors are exempt from content liveness (#175): a command regex
    # has no file to rot against, so it holds the scar alive unconditionally —
    # review_after is the freshness mechanism for command-only scars.
    if scar.command_anchors:
        return False

    # A scar with NO anchors at all is treated as dead (nothing to hold it alive).
    if not scar.path_anchors and not scar.pattern_anchors:
        return True

    for anchor in scar.path_anchors:
        if _path_anchor_live(anchor, ctx.tracked_paths):
            return False   # at least one live anchor → not all dead

    for pattern in scar.pattern_anchors:
        if _pattern_anchor_live(pattern, ctx, exclude_path=self_path):
            return False   # at least one live anchor → not all dead

    return True


def _self_rel(store: ScarStore, source: Path) -> str | None:
    """Repo-relative path of a scar file, for self-reference exclusion (#35)."""
    try:
        return str(source.relative_to(store.root))
    except ValueError:
        return None


def _split_top_level_alternation(pattern: str) -> list[str]:
    r"""Split a regex on its TOP-LEVEL `|` only (#213).

    A `|` inside a group `(a|b)` or a character class `[a|b]`, or escaped as
    `\|`, is not an alternation of the whole pattern — splitting there would
    manufacture phantom branches like `foo(a` and report false rot. Returns a
    single-element list when there is no top-level alternation.
    """
    branches: list[str] = []
    buf: list[str] = []
    depth = 0
    in_class = False
    escaped = False
    for ch in pattern:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\":
            buf.append(ch)
            escaped = True
            continue
        if in_class:
            buf.append(ch)
            if ch == "]":
                in_class = False
            continue
        if ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "|" and depth == 0:
            branches.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    branches.append("".join(buf))
    return branches


def _dead_pattern_branches(scar: Scar, ctx: RepoContext,
                           self_path: str | None) -> list[str]:
    """Top-level alternation branches that match nothing, for pattern anchors
    that are otherwise LIVE (#213).

    A live branch masks a dead one: `"a|b"` with `a` dead and `b` live reports
    clean today, so the over-escape trap scar #6 exists to catch hides behind
    the alternation. Anchors that are wholly dead are already reported by
    _dead_anchors — checking their branches too would double-report.
    """
    dead: list[str] = []
    for pat in scar.pattern_anchors:
        if not _pattern_anchor_live(pat, ctx, exclude_path=self_path):
            continue   # wholly dead — _dead_anchors already reports it
        branches = _split_top_level_alternation(pat)
        if len(branches) < 2:
            continue   # no alternation, nothing to sub-check
        for b in branches:
            if not _pattern_anchor_live(b, ctx, exclude_path=self_path):
                dead.append(b)
    return dead


def _dead_anchors(scar: Scar, ctx: RepoContext,
                  self_path: str | None) -> tuple[list[str], list[str]]:
    """The (dead_path_anchors, dead_pattern_anchors) for one scar — the SHARED
    reporting primitive used by both orphan and partial-rot detection so the two
    can never drift on what counts as a dead anchor (copy ≠ shared)."""
    dead_paths = [
        a for a in scar.path_anchors
        if not _path_anchor_live(a, ctx.tracked_paths)
    ]
    dead_patterns = [
        p for p in scar.pattern_anchors
        if not _pattern_anchor_live(p, ctx, exclude_path=self_path)
    ]
    return dead_paths, dead_patterns


def detect_orphans(store: ScarStore, ctx: RepoContext,
                   repo: Path | None = None) -> list[OrphanFinding]:
    """Scan active + challenged scars and return those whose every anchor is dead.

    Read-only. Never writes status. Skips unparseable scar files silently.

    *repo* (#109): when given, each finding's dead CONCRETE path anchors are
    checked against git's whole-history rename graph. An anchor that resolves
    to an unambiguous, currently-tracked rename target is reported in
    finding.renamed — advisory only, does not change orphan classification.
    Omitting *repo* (the default) skips rename detection entirely, same as
    before this option existed.
    """
    findings: list[OrphanFinding] = []
    resolver = RenameResolver(repo)
    tracked = set(ctx.tracked_paths)

    for source, scar in store.firing():
        try:
            self_path = _self_rel(store, source)
            # Single-source the orphan decision through anchors_all_dead so the
            # zero-anchor policy lives in exactly one place (Issue #33 AC#1 ethos,
            # one level down). The dead-anchor lists below are for REPORTING only.
            if not anchors_all_dead(scar, ctx, self_path=self_path):
                continue
            dead_paths, dead_patterns = _dead_anchors(scar, ctx, self_path)
            findings.append(OrphanFinding(
                scar_id=scar.id,
                dead_path_anchors=dead_paths,
                dead_pattern_anchors=dead_patterns,
                renamed=resolver.resolve(dead_paths, tracked),
            ))
        except Exception:
            # store.firing() already skips ParseError; this guards anything unexpected
            continue

    return findings


def detect_partial_rot(store: ScarStore, ctx: RepoContext,
                       repo: Path | None = None) -> list[PartialRotFinding]:
    """Scan active + challenged scars and return those still firing on ≥1 live
    anchor but carrying ≥1 dead anchor (#35 partial rot).

    Mutually exclusive with detect_orphans by construction: an all-dead scar is
    an orphan (skipped here), a fully-live scar has no dead anchors (skipped here).
    Read-only. Never writes status — partial rot is advisory, fixed by re-anchoring.

    *repo* (#109): same rename enrichment as detect_orphans — see its docstring.
    """
    findings: list[PartialRotFinding] = []
    resolver = RenameResolver(repo)
    tracked = set(ctx.tracked_paths)

    for source, scar in store.firing():
        try:
            self_path = _self_rel(store, source)
            # All-dead → that's an orphan, not partial rot. Skip.
            if anchors_all_dead(scar, ctx, self_path=self_path):
                continue
            dead_paths, dead_patterns = _dead_anchors(scar, ctx, self_path)
            dead_branches = _dead_pattern_branches(scar, ctx, self_path)
            if not dead_paths and not dead_patterns and not dead_branches:
                continue   # fully live → nothing rotted
            findings.append(PartialRotFinding(
                scar_id=scar.id,
                dead_path_anchors=dead_paths,
                dead_pattern_anchors=dead_patterns,
                renamed=resolver.resolve(dead_paths, tracked),
                dead_pattern_branches=dead_branches,
            ))
        except Exception:
            continue

    return findings


# ---------------------------------------------------------------------------
# Symbol drift (#99 Phase 3): a symbol anchor that still resolves by name but
# whose body shape changed since the scar's evidence commit. Read-only,
# advisory — gated behind the [symbols] extra + git + a non-shallow clone.
# Every failure mode degrades to "no finding", never raises.
# ---------------------------------------------------------------------------

def _drift_path(scar: Scar, anchor: str, tracked: set[str]) -> str | None:
    """Resolve the file for a symbol anchor: qualified path::Sym → that path;
    bare Sym → a path anchor that names a single tracked file. Else None."""
    if "::" in anchor:
        path = anchor.split("::", 1)[0]
        return path if path in tracked else None
    files = [p for p in scar.path_anchors if p in tracked]
    return files[0] if len(files) == 1 else None


def detect_symbol_drift(store: ScarStore, repo: Path) -> list[SymbolDriftFinding]:
    """Fingerprint each firing scar's symbol anchor at its evidence commit SHA
    vs HEAD and flag drift (Jaccard similarity < 1.0). Never raises."""
    repo = Path(repo)
    if not symbols.symbols_available() or _is_shallow(repo):
        return []
    try:
        tracked = set(build_repo_context(repo).tracked_paths)
    except GitError:
        return []

    findings: list[SymbolDriftFinding] = []
    for _source, scar in store.firing():
        try:
            shas = [s for s in _commit_shas(scar) if _reachable(repo, s)]
            if not scar.symbol_anchors or not shas:
                continue
            sha = shas[0]
            for anchor in scar.symbol_anchors:
                path = _drift_path(scar, anchor, tracked)
                if path is None:
                    continue
                show = _git(repo, "show", f"{sha}:{path}")
                if show.returncode != 0:
                    continue
                base_fp = symbols.fingerprint(anchor, path, show.stdout)
                try:
                    cur_src = (repo / path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                cur_fp = symbols.fingerprint(anchor, path, cur_src)
                if base_fp is None or cur_fp is None:
                    continue
                sim = symbols.jaccard(base_fp, cur_fp)
                if sim < 1.0:
                    findings.append(SymbolDriftFinding(
                        scar_id=scar.id, symbol=anchor, sha=sha, similarity=sim))
        except Exception:
            continue
    return findings
