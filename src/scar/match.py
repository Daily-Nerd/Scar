"""Anchor matching and injection ranking.

Scoring: anchor_strength x severity_weight x confidence.
Anchor strengths — content-pattern hit (2.5, a dead end re-appearing in new
code is the strongest signal) > path prefix (2.0) > pattern on the path (1.5).
"""

from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .model import Scar
from .store import ScarStore

SEVERITY_WEIGHT = {"low": 1, "medium": 2, "high": 3, "critical": 4}
DEFAULT_TOP_K = 3

# Cap the text one anchor regex may scan before search(). A valid-but-
# pathological anchor like (a+)+$ backtracks catastrophically and never returns
# on adversarial input; lint rejects those at the gate (lint._is_redos_prone),
# but this cap is belt-and-suspenders so an accidental/huge input can never blow
# up the read hot path — _anchor_signal runs against unbounded new_content, and
# orphan detection scans whole file bodies. 64 KiB comfortably fits real file
# paths and added-diff excerpts while bounding worst-case backtracking work; a
# few extra bytes of content are never worth a hung hook.
MAX_ANCHOR_SCAN = 64 * 1024


@dataclass(frozen=True)
class ScarMatch:
    scar: Scar
    source: Path
    rank: float
    anchor_strength: float
    matched_by: tuple[str, ...]
    path: str

    def to_dict(self) -> dict:
        # copy the whole Scar so a future model field can never silently
        # vanish from MCP responses (guarded by a fields() test)
        d = dict(self.scar.__dict__)
        d["anchors"] = {"paths": d.pop("path_anchors"),
                        "patterns": d.pop("pattern_anchors"),
                        "symbols": d.pop("symbol_anchors")}
        d.update(matched_by=list(self.matched_by),
                 anchor_strength=self.anchor_strength,
                 rank=self.rank, path=self.path, source=str(self.source))
        return d


# Signal types that prove the EDIT is related to the scar (not merely the
# file): a pattern hit inside the new content, or a symbol resolution.
# Path-prefix and pattern-on-path matches only prove file proximity — they
# render as one-line hints, not full bodies (precision engine).
CONTENT_SIGNALS = frozenset({"content_pattern", "symbol"})


def has_content_signal(match: ScarMatch) -> bool:
    """True iff the match carries an edit-content signal — full-body tier."""
    return bool(CONTENT_SIGNALS.intersection(match.matched_by))


def _path_anchor_matches(anchor: str, rel_path: str) -> bool:
    """One path anchor vs one path: prefix match. THE shared rule — orphan
    detection imports this so detection and injection can never disagree."""
    return rel_path.startswith(anchor.rstrip("/"))


def _pattern_anchor_matches(pattern: str, text: str) -> bool:
    """One pattern anchor vs one text (path OR content): case-insensitive
    regex search. Invalid regex -> False (lint's job; never crash the read
    path). THE shared rule — orphan detection imports this too."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return False
    # Cap scan length before search() — see MAX_ANCHOR_SCAN. This bounds the
    # HUGE-input case only; it does NOT stop catastrophic backtracking, which a
    # pathological anchor like (a+)+$ hits on ~30 chars, well under the cap. The
    # real ReDoS defense is the promote/CI gate: lint._is_redos_prone() errors on
    # nested-quantifier anchors, and the hot path only matches active (merged,
    # CI-linted) scars — so a pathological pattern can't reach here via the normal
    # workflow. Residual risk: a pattern hand-authored into a LOCAL .scars/ that
    # never hit CI can still backtrack here. Bounding arbitrary regex at runtime
    # needs a killable subprocess or a timeout-capable engine — both rejected to
    # keep this hot path stdlib-only and under the hook latency budget. See #88.
    return bool(rx.search(text[:MAX_ANCHOR_SCAN]))


def _read_source(abs_path: str) -> str | None:
    try:
        mtime = os.stat(abs_path).st_mtime_ns
    except OSError:
        return None
    return _read_source_cached(abs_path, mtime)


@functools.lru_cache(maxsize=256)
def _read_source_cached(abs_path: str, mtime: int) -> str | None:
    try:
        return Path(abs_path).read_text(encoding="utf8")
    except (OSError, UnicodeDecodeError):
        return None


def _symbol_anchor_hits(anchors: tuple[str, ...] | list[str], rel_path: str,
                        root: Path) -> bool:
    """True iff any symbol anchor resolves in the file at root/rel_path.
    Reads the file at most once per (path, mtime) via lru_cache and parses it
    once via symbols.resolve_any. Degrades to False when the extra is absent."""
    from . import symbols
    if not symbols.symbols_available():
        return False
    source = _read_source(str((root / rel_path).resolve()))
    if source is None:
        return False
    return symbols.resolve_any(anchors, rel_path, source)


def _anchor_signal(scar: Scar, rel_path: str, new_content: str,
                   root: Path | None = None) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    matched: list[str] = []
    for p in scar.path_anchors:
        if _path_anchor_matches(p, rel_path):
            score = max(score, 2.0)
            matched.append("path")
    if root is not None and scar.symbol_anchors:
        if _symbol_anchor_hits(scar.symbol_anchors, rel_path, root):
            score = max(score, 2.25)
            matched.append("symbol")
    for pat in scar.pattern_anchors:
        if _pattern_anchor_matches(pat, rel_path):
            score = max(score, 1.5)
            matched.append("path_pattern")
        if new_content and _pattern_anchor_matches(pat, new_content):
            score = max(score, 2.5)
            matched.append("content_pattern")
    return score, tuple(dict.fromkeys(matched))


def _match_target(firing: list, root: Path, rel_path: str,
                  new_content: str) -> list[ScarMatch]:
    """Rank one target against an already-loaded firing set (no disk I/O)."""
    ranked: list[ScarMatch] = []
    for source, scar in firing:
        strength, matched_by = _anchor_signal(scar, rel_path, new_content, root)
        if strength > 0:
            rank = strength * SEVERITY_WEIGHT.get(scar.severity, 2) * scar.confidence
            ranked.append(ScarMatch(scar=scar, source=source.relative_to(root),
                                    rank=rank, anchor_strength=strength,
                                    matched_by=matched_by, path=rel_path))
    ranked.sort(key=lambda m: -m.rank)
    return ranked


def merge_best_matches(match_lists: list[list[ScarMatch]],
                       top_k: int = DEFAULT_TOP_K) -> list[ScarMatch]:
    """Dedup matches across targets, keeping each scar's best rank."""
    best: dict[int | str, ScarMatch] = {}
    for matches in match_lists:
        for match in matches:
            key = match.scar.id if match.scar.id is not None else match.source.as_posix()
            if key not in best or match.rank > best[key].rank:
                best[key] = match
    return sorted(best.values(), key=lambda m: -m.rank)[:top_k]


def rank_matches_for_edit(store: ScarStore, target: Path, new_content: str,
                          top_k: int = DEFAULT_TOP_K) -> list[ScarMatch]:
    """Top-k firing scar matches relevant to editing `target`."""
    try:
        rel_path = str(Path(target).resolve().relative_to(store.root))
    except ValueError:
        return []
    return _match_target(store.firing(), store.root, rel_path, new_content)[:top_k]


def rank_matches_for_paths(store: ScarStore, paths: list[str], new_content: str,
                           top_k: int = DEFAULT_TOP_K) -> list[ScarMatch]:
    """Best matches across several paths — one store walk, not one per path."""
    firing = store.firing()
    lists = []
    for path in paths:
        try:
            rel = str((store.root / str(path)).resolve().relative_to(store.root))
        except ValueError:
            continue
        lists.append(_match_target(firing, store.root, rel, new_content)[:top_k])
    return merge_best_matches(lists, top_k)


def rank_for_edit(store: ScarStore, target: Path, new_content: str,
                  top_k: int = DEFAULT_TOP_K) -> list[Scar]:
    """Top-k firing scars (active + challenged) relevant to editing `target`."""
    return [m.scar for m in rank_matches_for_edit(store, target, new_content, top_k)]


def _diff_targets(diff_text: str) -> list[tuple[str, str]]:
    """Return (path, added_content) pairs from a unified diff."""
    targets: list[tuple[str, str]] = []
    current: str | None = None
    added: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            if current:
                targets.append((current, "\n".join(added)))
            raw = line[4:].strip()
            current = raw[2:] if raw.startswith("b/") else raw
            if current == "/dev/null":
                current = None
            added = []
        elif current and line.startswith("+") and not line.startswith("+++ "):
            added.append(line[1:])
    if current:
        targets.append((current, "\n".join(added)))
    return targets


def rank_matches_for_diff(store: ScarStore, diff_text: str,
                          top_k: int = DEFAULT_TOP_K) -> list[ScarMatch]:
    """Top-k firing scar matches across a unified diff (one store walk)."""
    firing = store.firing()
    lists = [_match_target(firing, store.root, rel_path, added)[:top_k]
             for rel_path, added in _diff_targets(diff_text)]
    return merge_best_matches(lists, top_k)
