"""Anchor matching and injection ranking.

Scoring: anchor_strength x severity_weight x confidence.
Anchor strengths — content-pattern hit (2.5, a dead end re-appearing in new
code is the strongest signal) > path prefix (2.0) > pattern on the path (1.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .model import Scar
from .store import ScarStore

SEVERITY_WEIGHT = {"low": 1, "medium": 2, "high": 3, "critical": 4}
DEFAULT_TOP_K = 3


@dataclass(frozen=True)
class ScarMatch:
    scar: Scar
    source: Path
    rank: float
    anchor_strength: float
    matched_by: tuple[str, ...]
    path: str

    def to_dict(self) -> dict:
        return {
            "id": self.scar.id,
            "type": self.scar.type,
            "title": self.scar.title,
            "severity": self.scar.severity,
            "confidence": self.scar.confidence,
            "status": self.scar.status,
            "body": self.scar.body,
            "anchors": {
                "paths": self.scar.path_anchors,
                "patterns": self.scar.pattern_anchors,
            },
            "matched_by": list(self.matched_by),
            "anchor_strength": self.anchor_strength,
            "rank": self.rank,
            "path": self.path,
            "source": str(self.source),
        }


def _anchor_signal(scar: Scar, rel_path: str, new_content: str) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    matched: list[str] = []
    for p in scar.path_anchors:
        if rel_path.startswith(p.rstrip("/")):
            score = max(score, 2.0)
            matched.append("path")
    for pat in scar.pattern_anchors:
        try:
            rx = re.compile(pat, re.IGNORECASE)
        except re.error:
            continue  # lint's job; never crash the read path
        if rx.search(rel_path):
            score = max(score, 1.5)
            matched.append("path_pattern")
        if new_content and rx.search(new_content):
            score = max(score, 2.5)
            matched.append("content_pattern")
    return score, tuple(dict.fromkeys(matched))


def rank_matches_for_edit(store: ScarStore, target: Path, new_content: str,
                          top_k: int = DEFAULT_TOP_K) -> list[ScarMatch]:
    """Top-k firing scar matches relevant to editing `target`."""
    try:
        rel_path = str(Path(target).resolve().relative_to(store.root))
    except ValueError:
        return []
    ranked: list[ScarMatch] = []
    for source, scar in store.firing():
        strength, matched_by = _anchor_signal(scar, rel_path, new_content)
        if strength > 0:
            rank = strength * SEVERITY_WEIGHT.get(scar.severity, 2) * scar.confidence
            ranked.append(ScarMatch(scar=scar, source=source.relative_to(store.root),
                                    rank=rank, anchor_strength=strength,
                                    matched_by=matched_by, path=rel_path))
    ranked.sort(key=lambda m: -m.rank)
    return ranked[:top_k]


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
    """Top-k firing scar matches across a unified diff."""
    best: dict[int | str, ScarMatch] = {}
    for rel_path, added_content in _diff_targets(diff_text):
        for match in rank_matches_for_edit(store, store.root / rel_path, added_content,
                                           top_k=top_k):
            key = match.scar.id if match.scar.id is not None else match.source.as_posix()
            if key not in best or match.rank > best[key].rank:
                best[key] = match
    return sorted(best.values(), key=lambda m: -m.rank)[:top_k]
