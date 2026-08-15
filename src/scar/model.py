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

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", re.DOTALL)
_URL_RE = re.compile(r"^https?://")


def is_valid_url(value: str) -> bool:
    """True iff value is an http(s) URL. Used by lint to flag durable-link
    evidence (url:) that isn't actually a link (#50)."""
    return bool(_URL_RE.match(value.strip()))


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
    symbol_anchors: list[str] = field(default_factory=list)
    command_anchors: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    expires_condition: str = ""
    review_after: str = ""
    violation: str = ""
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
        lines += [f"  - symbol: {s}" for s in self.symbol_anchors]
        lines += [f'  - command: "{c}"' for c in self.command_anchors]
        if self.evidence:
            lines.append("evidence:")
            lines += [f"  - {e}" for e in self.evidence]
        if self.expires_condition or self.review_after:
            lines.append("expires:")
            if self.expires_condition:
                lines.append(f'  condition: "{self.expires_condition}"')
            if self.review_after:
                lines.append(f"  review_after: {self.review_after}")
        if self.violation:
            lines.append(f'violation: "{self.violation}"')
        lines += [f"status: {self.status}", "---", "", self.body.strip(), ""]
        return "\n".join(lines)


_TRAILING_COMMENT_RE = re.compile(r"\s+#.*$")


def _unquote(value: str) -> str:
    # Remove exactly ONE matching wrapper-quote pair — never edge quote
    # characters that are part of the value (#202: to_text always adds one
    # double-quote pair, so pair-removal is the exact inverse; char-wise
    # .strip('"') corrupted regexes with edge/embedded quotes). A trailing
    # "  # comment" after the closing quote is dropped first: quoted values
    # skip _strip_inline_comment (a '#' inside quotes is data), which
    # otherwise fuses the template's trailing comments into the value.
    if value[:1] in ('"', "'"):
        closing = value.rfind(value[0])
        if closing > 0:
            tail = value[closing + 1:]
            if not tail or _TRAILING_COMMENT_RE.fullmatch(tail):
                return value[1:closing]
    return value


def _strip_inline_comment(value: str) -> str:
    # Strip an unquoted inline comment (YAML: a '#' after whitespace starts a
    # comment). The template ships '# ...' on every field; a copied-but-uncleaned
    # comment otherwise lands in the value — silently for confidence/severity,
    # loudly for type. Quoted values are left intact so a '#' inside a quoted
    # scalar (title, condition) stays data. See issue #69.
    if '"' not in value and "'" not in value:
        value = re.sub(r"\s+#.*$", "", value).rstrip()
    return value


def _field(front: str, name: str, default: str = "", strip_comment: bool = True) -> str:
    m = re.search(rf"^\s*{name}:\s*(.+?)\s*$", front, re.MULTILINE)
    if not m:
        return default
    value = m.group(1)
    # A free-text scalar (title) can legitimately contain a '#' (e.g. an issue
    # ref) and is written unquoted by to_text(); stripping it as a comment
    # truncates the value on round-trip (#91). Skip the strip for those fields.
    if strip_comment:
        value = _strip_inline_comment(value)
    return value


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

    # Evidence values get the same inline-comment strip as scalar fields (#69,
    # #144): the template documents evidence lines WITH trailing comments, and
    # an unstripped comment silently voids the receipt downstream (a commit SHA
    # with a comment fails _commit_shas' regex, hiding it from the
    # unreachable-evidence check). Quoted values keep their '#'.
    evidence = [f"{m1.group(1)}: {_strip_inline_comment(m1.group(2)).strip().strip('\"')}"
                for m1 in re.finditer(
        r"^\s*-\s*(commit|pr|issue|incident|note|url):\s*(.+)\s*$", front, re.MULTILINE)]

    return Scar(
        type=_field(front, "type", "deadend"),
        title=_field(front, "title", strip_comment=False),
        id=scar_id,
        severity=_field(front, "severity", "medium"),
        confidence=confidence,
        created=_field(front, "created"),
        authors=authors,
        path_anchors=[_strip_inline_comment(p).strip('"').strip("'")
                      for p in re.findall(r"^\s*-\s*path:\s*(.+?)\s*$", front, re.MULTILINE)],
        pattern_anchors=[_unquote(p.strip())
                         for p in re.findall(r"^\s*-\s*pattern:\s*(.+?)\s*$", front, re.MULTILINE)],
        symbol_anchors=[_strip_inline_comment(s).strip('"').strip("'")
                        for s in re.findall(r"^\s*-\s*symbol:\s*(.+?)\s*$", front, re.MULTILINE)],
        # Raw-regex contract, same as pattern/violation: wrapper quotes
        # removed, nothing un-escaped. Single-quote wrappers removed too so
        # the silently-dead single-quoted trap (daimon 0021) cannot recur.
        command_anchors=[_unquote(c.strip())
                         for c in re.findall(r"^\s*-\s*command:\s*(.+?)\s*$", front, re.MULTILINE)],
        evidence=evidence,
        expires_condition=_unquote(_field(front, "condition")),
        review_after=_field(front, "review_after"),
        violation=_field(front, "violation").strip('"'),
        status=_field(front, "status", "active"),
        body=body.strip(),
    )
