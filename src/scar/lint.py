"""Lint rules. Each rule exists because the failure already happened once:
no-frontmatter = daimon 0004 (born unable to fire); the rest harden the
contract in .scars/README.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import SEVERITIES, STATUSES, TYPES, ParseError, parse_scar_text


@dataclass
class Finding:
    level: str  # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.message}"


def lint_text(text: str) -> list[Finding]:
    try:
        scar = parse_scar_text(text)
    except ParseError:
        return [Finding("error", "missing YAML frontmatter — this scar can NEVER fire")]

    findings = []
    if scar.type not in TYPES:
        findings.append(Finding("error", f"unknown type '{scar.type}' (expected one of {', '.join(TYPES)})"))
    if not scar.title:
        findings.append(Finding("error", "missing title"))
    if scar.severity not in SEVERITIES:
        findings.append(Finding("error", f"invalid severity '{scar.severity}'"))
    if scar.status not in STATUSES:
        findings.append(Finding("error", f"invalid status '{scar.status}'"))
    if not scar.path_anchors and not scar.pattern_anchors:
        findings.append(Finding("error", "no anchors — scar protects nothing"))
    for pat in scar.pattern_anchors:
        try:
            re.compile(pat)
        except re.error as exc:
            findings.append(Finding("error", f"invalid pattern anchor /{pat}/: {exc}"))
    if not scar.evidence:
        findings.append(Finding("warning", "no evidence links — challengeable on sight"))
    if not scar.body:
        findings.append(Finding("warning", "empty body — future readers get no why"))
    return findings
