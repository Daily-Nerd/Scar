"""Git rename following (#109).

A path anchor whose file was `git mv`-ed is indistinguishable, to the anchor
liveness check in orphan.py, from one whose file was deleted outright. This
module answers "was this dead path anchor renamed, and to what?" so orphan
and partial-rot findings can say `renamed: <old> -> <new>` instead of a bare
dead, and `scar orphan --fix-renames` can repair the anchor mechanically.

Off the hot path, by construction: nothing here is imported by match.py or
hooks.py. Detection runs ONLY from explicit commands (`scar orphan`, `scar
lint`, `scar status`), and only for path anchors already found dead — so the
one whole-history `git log` walk this module performs is bounded to firing
scans that already have a dead anchor to investigate, never the edit-time hot
path (#109 AC: "no git invocation added to match.py/hooks.py").

READ-ONLY module: build_rename_map / resolve_rename / RenameResolver. The
write path (a surgical single-line anchor fix for `scar orphan --fix-renames`)
lands separately.
"""

from __future__ import annotations

from pathlib import Path

from .evidence import _git, _is_shallow

# Directory anchors (trailing '/') and glob-shaped anchors are out of scope —
# renames apply to concrete tracked files only (#109 design).
_GLOB_CHARS = ("*", "?", "[")


def is_concrete_path_anchor(anchor: str) -> bool:
    """True for a path anchor renames can apply to: a single file, not a
    directory anchor or a glob-shaped pattern."""
    if anchor.endswith("/"):
        return False
    return not any(ch in anchor for ch in _GLOB_CHARS)


def build_rename_map(repo: Path) -> dict[str, list[str]] | None:
    """Whole-history old-path -> [new-path, ...] rename map, one `git log`
    walk over the full history (`-M`, `--diff-filter=R`). `--reverse` orders
    entries oldest-first so a chain (A->B->C) resolves in the order it
    actually happened, though resolve_rename() itself is order-independent —
    it just follows the graph.

    Returns None (never {}) when detection cannot be trusted: not a git repo,
    a shallow clone (partial history -> renames older than the truncation
    point are invisible, same posture as evidence.py's shallow skip), or any
    other git failure. Callers must treat None as "skip silently", the same
    contract as _is_shallow gates elsewhere in this codebase.
    """
    repo = Path(repo)
    try:
        if _is_shallow(repo):
            return None
        proc = _git(repo, "log", "--diff-filter=R", "-M", "--name-status",
                    "--format=", "--reverse")
    except OSError:
        return None  # git binary missing — degrade to "no finding", never raise
    if proc.returncode != 0:
        return None  # not a git repo / git failed

    rename_map: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        if not line.startswith("R"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        _status, old, new = parts
        rename_map.setdefault(old, []).append(new)
    return rename_map


def resolve_rename(rename_map: dict[str, list[str]], start: str,
                   tracked: set[str]) -> str | None:
    """Follow the rename chain from *start* (e.g. A->B->C) to its terminal
    name. Returns the terminal name only if EVERY hop is unambiguous (exactly
    one recorded target) and the terminal name is currently tracked.

    None when: no rename was ever recorded from *start* (deleted, not
    renamed); any hop has more than one distinct recorded target (ambiguous —
    refuse to guess); a cycle is detected; or the terminal name is not in
    *tracked* (renamed, but the destination is itself gone now).
    """
    current = start
    seen = {current}
    while True:
        targets = rename_map.get(current)
        if not targets:
            break
        distinct = set(targets)
        if len(distinct) != 1:
            return None  # ambiguous hop
        nxt = next(iter(distinct))
        if nxt in seen:
            return None  # cycle guard
        seen.add(nxt)
        current = nxt

    if current == start:
        return None  # no rename recorded at all
    return current if current in tracked else None


class RenameResolver:
    """Lazy, cached wrapper around build_rename_map/resolve_rename for a scan
    over many scars: the whole-history walk runs at most ONCE per detection
    run (on first dead concrete path anchor encountered), not once per anchor
    or per scar — bounded cost (#109)."""

    def __init__(self, repo: Path | None):
        self._repo = repo
        self._map: dict[str, list[str]] | None = None
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._repo is not None:
            self._map = build_rename_map(self._repo)

    def resolve(self, dead_paths: list[str], tracked: set[str]) -> dict[str, str]:
        """Resolve every CONCRETE dead path anchor in *dead_paths* to its
        rename target, if any. Directory/glob anchors are silently skipped.
        Returns {} (never raises) when the repo is unavailable, shallow, or
        no anchor resolves."""
        if self._repo is None:
            return {}
        candidates = [a for a in dead_paths if is_concrete_path_anchor(a)]
        if not candidates:
            return {}
        self._ensure_loaded()
        if not self._map:
            return {}
        out: dict[str, str] = {}
        for anchor in candidates:
            target = resolve_rename(self._map, anchor, tracked)
            if target is not None:
                out[anchor] = target
        return out
