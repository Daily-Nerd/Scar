"""Anchor matching and injection ranking.

Scoring: anchor_strength x severity_weight x confidence.
Anchor strengths — content-pattern hit (2.5, a dead end re-appearing in new
code is the strongest signal) > path prefix (2.0) > pattern on the path (1.5).
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import Scar
from .store import ScarStore

SEVERITY_WEIGHT = {"low": 1, "medium": 2, "high": 3, "critical": 4}
DEFAULT_TOP_K = 3


def _anchor_strength(scar: Scar, rel_path: str, new_content: str) -> float:
    score = 0.0
    for p in scar.path_anchors:
        if rel_path.startswith(p.rstrip("/")):
            score = max(score, 2.0)
    for pat in scar.pattern_anchors:
        try:
            rx = re.compile(pat, re.IGNORECASE)
        except re.error:
            continue  # lint's job; never crash the read path
        if rx.search(rel_path):
            score = max(score, 1.5)
        if new_content and rx.search(new_content):
            score = max(score, 2.5)
    return score


def rank_for_edit(store: ScarStore, target: Path, new_content: str,
                  top_k: int = DEFAULT_TOP_K) -> list[Scar]:
    """Top-k firing scars (active + challenged) relevant to editing `target`."""
    try:
        rel_path = str(Path(target).resolve().relative_to(store.root))
    except ValueError:
        return []
    ranked = []
    for _, scar in store.firing():
        strength = _anchor_strength(scar, rel_path, new_content)
        if strength > 0:
            rank = strength * SEVERITY_WEIGHT.get(scar.severity, 2) * scar.confidence
            ranked.append((rank, scar))
    ranked.sort(key=lambda t: -t[0])
    return [s for _, s in ranked[:top_k]]
