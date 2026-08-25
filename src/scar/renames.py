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

Detection (build_rename_map / resolve_rename / RenameResolver) is READ-ONLY.
apply_rename_fix is the ONE write path, and it is surgical: a single anchor
line is text-replaced in place, never a parse+reserialize of the whole scar.
Landmine #4: promote's parse->Scar.to_text() roundtrip once silently dropped
the expires/evidence blocks on a hand-authored file — reserializing loses
whatever the model doesn't round-trip. A rename fix must not repeat that.
"""

from __future__ import annotations

import re
from pathlib import Path

from .evidence import _git, _is_shallow
from .model import _strip_inline_comment, _unquote

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


# ---------------------------------------------------------------------------
# Write path — the ONLY place this module mutates a file on disk.
# ---------------------------------------------------------------------------

_PATH_ANCHOR_LINE_RE = re.compile(r"^\s*-\s*path:\s*(.+?)\s*$")
_SYMBOL_ANCHOR_LINE_RE = re.compile(r"^\s*-\s*symbol:\s*(.+?)\s*$")

# Kind-aware line matchers for apply_anchor_rewrite. Only anchor kinds the
# model actually parses (path/pattern/symbol — see lint.py's
# _UNSUPPORTED_ANCHOR) are meaningful rewrite targets; "pattern" is
# deliberately absent — pattern regeneration is a v1 cut (#111 design), so
# there is no writer for it yet, and an unrecognized kind here is a no-op,
# never a guess.
_ANCHOR_LINE_RE = {
    "path": _PATH_ANCHOR_LINE_RE,
    "symbol": _SYMBOL_ANCHOR_LINE_RE,
}


def apply_anchor_rewrite(path: Path, anchor_kind: str, renamed: dict[str, str]) -> bool:
    """Surgically rewrite `- <anchor_kind>: <old>` anchor lines in the scar
    file at *path* to `<new>` for every old->new pair in *renamed*. Every
    other byte in the file — formatting, comments, quoting, the body — is
    untouched.

    This is a targeted substring replace confined to the exact span matched
    by the kind-specific anchor-line regex, never a parse+reserialize
    (landmine #4). Returns True iff at least one line was changed (file is
    rewritten only then); False leaves the file byte-identical and unwritten
    — including when *anchor_kind* isn't a recognized rewrite target.
    """
    if not renamed:
        return False
    line_re = _ANCHOR_LINE_RE.get(anchor_kind)
    if line_re is None:
        return False
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    changed = False
    for i, line in enumerate(lines):
        m = line_re.match(line)
        if not m:
            continue
        raw_val = m.group(1)
        stripped = _unquote(_strip_inline_comment(raw_val))
        new_val = renamed.get(stripped)
        if new_val is None:
            continue
        idx = raw_val.find(stripped)
        if idx == -1:
            continue  # defensive; stripped is derived from raw_val, always found
        val_start = m.start(1)
        abs_start = val_start + idx
        abs_end = abs_start + len(stripped)
        lines[i] = line[:abs_start] + new_val + line[abs_end:]
        changed = True
    if changed:
        path.write_text("\n".join(lines), encoding="utf-8")
    return changed


def apply_rename_fix(path: Path, renamed: dict[str, str]) -> bool:
    """Surgically rewrite `- path: <old>` anchor lines in the scar file at
    *path* to `<new>` for every old->new pair in *renamed* (#109). Thin
    wrapper over `apply_anchor_rewrite(path, "path", renamed)` — kept as its
    own name because it's the established call site (orphan --fix-renames)
    and the byte-identity test contract for it predates the kind-aware
    generalization (#111).
    """
    return apply_anchor_rewrite(path, "path", renamed)
