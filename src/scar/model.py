"""The scar model — the ONE parser/serializer for the whole system.

Frontmatter is a constrained YAML subset parsed line-wise on purpose: zero
dependencies keeps hook startup ~20ms, and the format is ours to constrain.
Anything this module can't parse, no SCAR tool fires on — so every consumer
must go through here (hooks included, eventually) to prevent parser drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TYPES = ("deadend", "fence", "landmine")
SEVERITIES = ("low", "medium", "high", "critical")
STATUSES = ("candidate", "active", "challenged", "archived", "orphaned", "template")

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


class ParseError(ValueError):
    """Text is not a scar (no/malformed frontmatter)."""


@dataclass
class Scar:
    type: str = "deadend"
    title: str = ""
    id: int | None = None
    severity: str = "medium"
    confidence: float = 0.5
    created: str = ""
    authors: list[str] = field(default_factory=list)
    path_anchors: list[str] = field(default_factory=list)
    pattern_anchors: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    expires_condition: str = ""
    review_after: str = ""
    status: str = "active"
    body: str = ""

    def to_text(self) -> str:
        lines = ["---"]
        if self.id is not None:
            lines.append(f"id: {self.id}")
        lines += [f"type: {self.type}", f"title: {self.title}",
                  f"severity: {self.severity}", f"confidence: {self.confidence}"]
        if self.created:
            lines.append(f"created: {self.created}")
        if self.authors:
            lines.append("authors: [" + ", ".join(f'"{a}"' for a in self.authors) + "]")
        lines.append("anchors:")
        lines += [f"  - path: {p}" for p in self.path_anchors]
        lines += [f'  - pattern: "{p}"' for p in self.pattern_anchors]
        if self.evidence:
            lines.append("evidence:")
            lines += [f"  - {e}" for e in self.evidence]
        if self.expires_condition or self.review_after:
            lines.append("expires:")
            if self.expires_condition:
                lines.append(f'  condition: "{self.expires_condition}"')
            if self.review_after:
                lines.append(f"  review_after: {self.review_after}")
        lines += [f"status: {self.status}", "---", "", self.body.strip(), ""]
        return "\n".join(lines)


def _field(front: str, name: str, default: str = "") -> str:
    m = re.search(rf"^{name}:\s*(.+?)\s*$", front, re.MULTILINE)
    return m.group(1) if m else default


def parse_scar_text(text: str) -> Scar:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ParseError("no YAML frontmatter (--- block) — scar can never fire")
    front, body = m.groups()

    try:
        confidence = float(_field(front, "confidence", "0.5"))
    except ValueError:
        confidence = 0.5
    raw_id = _field(front, "id")
    try:
        scar_id: int | None = int(raw_id) if raw_id else None
    except ValueError:
        scar_id = None

    authors_raw = _field(front, "authors")
    authors = [a.strip().strip('"').strip("'")
               for a in authors_raw.strip("[]").split(",") if a.strip()] if authors_raw else []

    evidence = [f"{m1.group(1)}: {m1.group(2)}" for m1 in re.finditer(
        r"^\s*-\s*(commit|pr|incident|note):\s*\"?([^\"\n]+?)\"?\s*$", front, re.MULTILINE)]

    return Scar(
        type=_field(front, "type", "deadend"),
        title=_field(front, "title"),
        id=scar_id,
        severity=_field(front, "severity", "medium"),
        confidence=confidence,
        created=_field(front, "created"),
        authors=authors,
        path_anchors=re.findall(r"^\s*-\s*path:\s*(\S+)\s*$", front, re.MULTILINE),
        pattern_anchors=[p.strip().strip('"')
                         for p in re.findall(r"^\s*-\s*pattern:\s*(.+?)\s*$", front, re.MULTILINE)],
        evidence=evidence,
        expires_condition=_field(front, "condition").strip('"'),
        review_after=_field(front, "review_after"),
        status=_field(front, "status", "active"),
        body=body.strip(),
    )
