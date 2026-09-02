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
class MatchCensus:
    """What matched an edit BEFORE the fatigue budget truncated it (#286).

    `count` on a firing row is min(matched, top_k), so co-fires per edit have
    been capped at DEFAULT_TOP_K in the log for as long as the cap existed.
    This is taken from _match_target's full ranked list, and the split is the
    same one _select_top uses to tier the budget: `content` matched the edit
    itself (act-proof), `path_only` matched nothing but the file's location.
    `content + path_only == total` always."""
    total: int
    content: int
    path_only: int

    def to_dict(self) -> dict:
        return {"total": self.total, "content": self.content,
                "path_only": self.path_only}


def census_of(ranked: list["ScarMatch"]) -> MatchCensus:
    content = sum(1 for m in ranked if has_content_signal(m))
    return MatchCensus(total=len(ranked), content=content,
                       path_only=len(ranked) - content)


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
                        "symbols": d.pop("symbol_anchors"),
                        "commands": d.pop("command_anchors")}
        d.update(matched_by=list(self.matched_by),
                 anchor_strength=self.anchor_strength,
                 rank=self.rank, path=self.path, source=str(self.source))
        return d


# Signal types that prove the ACT is related to the scar (not merely the
# file): a pattern hit inside the new content, a symbol resolution, or a
# command anchor matching the command about to run. Path-prefix and
# pattern-on-path matches only prove file proximity — they render as
# one-line hints, not full bodies (precision engine).
CONTENT_SIGNALS = frozenset({"content_pattern", "symbol", "command"})


def has_content_signal(match: ScarMatch) -> bool:
    """True iff the match carries an edit-content signal — full-body tier."""
    return bool(CONTENT_SIGNALS.intersection(match.matched_by))


def _path_anchor_matches(anchor: str, rel_path: str) -> bool:
    """One path anchor vs one path: prefix match. THE shared rule — orphan
    detection imports this so detection and injection can never disagree."""
    return rel_path.startswith(anchor.rstrip("/"))


def _pattern_anchor_matches(pattern: str, text: str,
                            limit: int | None = MAX_ANCHOR_SCAN) -> bool:
    """One pattern anchor vs one text (path OR content): case-insensitive
    regex search. Invalid regex -> False (lint's job; never crash the read
    path). THE shared rule — orphan detection imports this too.

    *limit* caps how much of *text* is scanned; ``None`` scans all of it.
    The default is the read hot path's bound and must stay that way. Offline
    callers (liveness/orphan detection) pass ``None``: their input is a
    known-size file already capped at MAX_CONTENT_BYTES, they are not on the
    latency budget, and a bound there does not protect anything — it just
    reports live anchors as dead once a file grows past it (#259)."""
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
    return bool(rx.search(text if limit is None else text[:limit]))


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


def _select_top(matches: list["ScarMatch"], top_k: int) -> list["ScarMatch"]:
    """Tier BEFORE truncating (#185): the fatigue budget's slots go to
    act-proof (content-signal) matches first, then fill with path-proximity
    matches. Without this, a content-signal match ranked below top_k by
    louder path-only scars is deleted outright instead of the path-only
    match being demoted to a one-liner — the opposite of the precision
    engine's contract. Order within each tier stays rank-descending."""
    content = [m for m in matches if has_content_signal(m)]
    proximity = [m for m in matches if not has_content_signal(m)]
    return (content + proximity)[:top_k]


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
    return _select_top(sorted(best.values(), key=lambda m: -m.rank), top_k)


def rank_and_census_for_edit(store: ScarStore, target: Path, new_content: str,
                             top_k: int = DEFAULT_TOP_K,
                             firing: list | None = None,
                             ) -> tuple[list[ScarMatch], MatchCensus | None]:
    """rank_matches_for_edit plus the pre-truncation census (#286). The census
    is None, not zeros, when the target is outside the store: zeros would
    claim an edit was observed and matched nothing."""
    try:
        rel_path = str(Path(target).resolve().relative_to(store.root))
    except ValueError:
        return [], None
    if firing is None:
        firing = store.firing()
    ranked = _match_target(firing, store.root, rel_path, new_content)
    return _select_top(ranked, top_k), census_of(ranked)


def rank_matches_for_edit(store: ScarStore, target: Path, new_content: str,
                          top_k: int = DEFAULT_TOP_K,
                          firing: list | None = None) -> list[ScarMatch]:
    """Top-k firing scar matches relevant to editing `target`. Pass `firing`
    when the caller already holds a store.scan() result (#186) — the hook hot
    path must not trigger a second directory parse."""
    return rank_and_census_for_edit(store, target, new_content, top_k, firing)[0]


def rank_and_census_for_command(store: ScarStore, command: str,
                                top_k: int = DEFAULT_TOP_K,
                                firing: list | None = None,
                                ) -> tuple[list[ScarMatch], MatchCensus]:
    """rank_matches_for_command plus the pre-truncation census (#286). Every
    command hit is content-signal, so path_only is 0 by construction."""
    ranked = _rank_command(store, command, firing)
    return ranked[:top_k], census_of(ranked)


def rank_matches_for_command(store: ScarStore, command: str,
                             top_k: int = DEFAULT_TOP_K,
                             firing: list | None = None) -> list[ScarMatch]:
    """Top-k firing scars whose command anchors match a shell command about
    to execute (#175). Command anchors are matched ONLY here — never against
    edit paths or content — and a hit is act-proof (full-body tier), because
    the command IS the mistake the scar warns about."""
    return _rank_command(store, command, firing)[:top_k]


def _rank_command(store: ScarStore, command: str,
                  firing: list | None) -> list[ScarMatch]:
    ranked: list[ScarMatch] = []
    if firing is None:
        firing = store.firing()
    for source, scar in firing:
        if not scar.command_anchors:
            continue
        if not any(_pattern_anchor_matches(c, command) for c in scar.command_anchors):
            continue
        rank = 2.5 * SEVERITY_WEIGHT.get(scar.severity, 2) * scar.confidence
        ranked.append(ScarMatch(scar=scar, source=source.relative_to(store.root),
                                rank=rank, anchor_strength=2.5,
                                matched_by=("command",), path=command))
    ranked.sort(key=lambda m: -m.rank)
    return ranked


def rank_and_census_for_targets(store: ScarStore,
                                targets: list[tuple[Path | str, str]],
                                top_k: int = DEFAULT_TOP_K,
                                firing: list | None = None,
                                ) -> tuple[list[ScarMatch], dict[str, MatchCensus]]:
    """rank_matches_for_targets plus one census per target, keyed by path
    relative to the store (#286). Taken before the per-target cut and before
    merge_best_matches dedups across files, because the multi-file writers
    log one row per file. A target outside the store gets no entry."""
    if firing is None:
        firing = store.firing()
    lists = []
    census: dict[str, MatchCensus] = {}
    for target, new_content in targets:
        path = Path(target)
        path = path if path.is_absolute() else store.root / path
        try:
            rel = str(path.resolve().relative_to(store.root))
        except ValueError:
            continue
        ranked = _match_target(firing, store.root, rel, new_content)
        census[rel] = census_of(ranked)
        lists.append(_select_top(ranked, top_k))
    return merge_best_matches(lists, top_k), census


def rank_matches_for_targets(store: ScarStore,
                             targets: list[tuple[Path | str, str]],
                             top_k: int = DEFAULT_TOP_K,
                             firing: list | None = None) -> list[ScarMatch]:
    """Best matches across path/content pairs with one loaded firing set."""
    return rank_and_census_for_targets(store, targets, top_k, firing)[0]


def rank_matches_for_paths(store: ScarStore, paths: list[str], new_content: str,
                           top_k: int = DEFAULT_TOP_K) -> list[ScarMatch]:
    """Best matches across several paths — one store walk, not one per path."""
    return rank_matches_for_targets(
        store, [(path, new_content) for path in paths], top_k)


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
    return rank_matches_for_targets(store, _diff_targets(diff_text), top_k)


@dataclass(frozen=True)
class Violation:
    scar: Scar
    source: Path      # scar file relative to root
    path: str         # edited file that tripped it
    excerpt: str      # line containing the first match (trimmed ~120 chars)


def _violation_excerpt(pattern: str, text: str) -> str | None:
    """First line of `text` matching `pattern`, trimmed to ~120 chars.
    Searches the whole capped text for the pattern, then extracts the line
    containing the match. Invalid regex -> None (lint's job; never crash the
    read path — mirrors _pattern_anchor_matches)."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None
    capped = text[:MAX_ANCHOR_SCAN]
    m = rx.search(capped)
    if m is None:
        return None
    # Extract the line containing the match
    line_start = capped.rfind("\n", 0, m.start()) + 1
    line_end = capped.find("\n", m.start())
    if line_end == -1:
        line_end = len(capped)
    return capped[line_start:line_end][:120]


def _armed_candidates(firing: list, root: Path, rel_path: str):
    """(scar, rel_source) for every firing scar that COULD produce a violation
    on rel_path: armed with a violation regex, not the scar's own file, and
    anchored here.

    ONE definition with two consumers — the violation matcher below and the
    verdict-expectation check (#277). Kept shared on purpose: if the two ever
    disagreed, the tool would either expect a verdict for a scar the matcher
    never evaluates (an expectation that can never resolve) or evaluate one it
    never expected (a violation with no matching expectation).
    """
    for source, scar in firing:
        if not scar.violation:
            continue
        rel_source = source.relative_to(root)
        # A scar's own body quotes the forbidden construct by design — never
        # count edits to the scar file itself as a violation (#148). Exclusion
        # is per-file only: .scars/-anchored violations (e.g. scar #5) must
        # still fire on OTHER scar files.
        if str(rel_source) == rel_path:
            continue
        # Candidacy: path-proximity anchors only (empty content -> content
        # anchors can never contribute, mirroring the injection anchor gate).
        strength, _ = _anchor_signal(scar, rel_path, "", root)
        if strength <= 0:
            continue
        yield scar, rel_source


def _violations_for_target(firing: list, root: Path, rel_path: str,
                           new_content: str) -> list["Violation"]:
    out: list[Violation] = []
    for scar, rel_source in _armed_candidates(firing, root, rel_path):
        excerpt = _violation_excerpt(scar.violation, new_content)
        if excerpt is None:
            continue
        out.append(Violation(scar=scar, source=rel_source,
                             path=rel_path, excerpt=excerpt))
    return out


def armed_scar_ids(store: ScarStore, rel_path: str) -> list[int]:
    """Ids of the scars a posttool verdict is owed for on this path (#277).

    Non-empty means a violation was POSSIBLE here, so silence from posttool is
    an unresolved verdict rather than a clean result. Empty means a violation
    was impossible, and nothing is owed.
    """
    return [s.id for s, _ in _armed_candidates(store.firing(), store.root, rel_path)
            if s.id is not None]


def find_violations(store: ScarStore, rel_path: str, new_content: str) -> list[Violation]:
    """Firing scars whose `violation` regex matches new_content on an
    already-anchored path — post-edit tripwire, not injection."""
    return _violations_for_target(store.firing(), store.root, rel_path, new_content)


def find_violations_for_targets(store: ScarStore,
                                targets: list[tuple[Path | str, str]]) -> list[Violation]:
    """Violation matches across path/content pairs with one store walk."""
    firing = store.firing()
    out: list[Violation] = []
    for target, new_content in targets:
        path = Path(target)
        path = path if path.is_absolute() else store.root / path
        try:
            rel = str(path.resolve().relative_to(store.root))
        except ValueError:
            continue
        out.extend(_violations_for_target(firing, store.root, rel, new_content))
    return out


def find_violations_for_diff(store: ScarStore, diff_text: str) -> list[Violation]:
    """Same as find_violations, scanning only added lines per diff file."""
    return find_violations_for_targets(store, _diff_targets(diff_text))
