"""Lint rules. Each rule exists because the failure already happened once:
no-frontmatter = daimon 0004 (born unable to fire); the rest harden the
contract in .scars/README.md.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .model import SEVERITIES, STATUSES, TYPES, ParseError, is_valid_url, parse_scar_text


# A valid regex can still be a ReDoS weapon: a quantified group that is itself
# quantified — (a+)+, (a*)*, (a+)*, (a*)+, ([a-z]+)* — backtracks catastrophically.
# re.compile accepts it (the syntax is fine) so the plain compile-check below lets
# it through; search() then hangs on adversarial input on the read hot path.
# Reject the classic nested-quantifier forms at the gate so a pathological anchor
# can never be promoted to an active (firing) scar. Conservative by construction:
# the group must (a) open with a real, unescaped '(' — so an escaped literal paren
# like redis\.get\( is ignored — (b) contain its own '+'/'*' quantifier, and
# (c) be immediately followed by another '+'/'*'. Ordinary anchors
# (redis\.get\(, output\.render\(, TODO|FIXME) never satisfy all three.
_NESTED_QUANTIFIER = re.compile(r"(?<!\\)\((?:\?[:=!])?[^()]*[*+][^()]*\)[*+]")


def _is_redos_prone(pattern: str) -> bool:
    return bool(_NESTED_QUANTIFIER.search(pattern))


# The model extracts `path`/`pattern`/`symbol` anchors; any other anchor key is
# silently dropped, so a SPEC-following author who writes `- touch:` or
# `- breaks:` gets false protection (the scar never fires on that anchor).
# Scan the raw frontmatter and warn — but only warn, never error: flagging these
# as errors would break existing repos that already carry such anchors.
# `symbol:` anchors ARE parsed into Scar.symbol_anchors but not yet enforced by
# the matcher (tree-sitter comes in Phase 2) — see the resolution-pending
# warning below, which is separate from this unsupported-anchor warning.
_UNSUPPORTED_ANCHOR = re.compile(
    r"^\s*-\s*(touch|breaks):", re.MULTILINE)


@dataclass
class Finding:
    level: str  # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.message}"


def lint_text(text: str, today: str | None = None) -> list[Finding]:
    try:
        scar = parse_scar_text(text)
    except ParseError:
        return [Finding("error", "missing YAML frontmatter — this scar can NEVER fire")]
    today = today or time.strftime("%Y-%m-%d")

    findings = []
    if scar.type not in TYPES:
        findings.append(Finding("error", f"unknown type '{scar.type}' (expected one of {', '.join(TYPES)})"))
    if not scar.title:
        findings.append(Finding("error", "missing title"))
    if scar.severity not in SEVERITIES:
        findings.append(Finding("error", f"invalid severity '{scar.severity}'"))
    if scar.status not in STATUSES:
        findings.append(Finding("error", f"invalid status '{scar.status}'"))
    if not scar.path_anchors and not scar.pattern_anchors and not scar.symbol_anchors:
        findings.append(Finding("error", "no anchors — scar protects nothing"))
    # Scan the raw frontmatter for anchor keys the model cannot represent.
    front = text.split("\n---", 1)[0]
    for kind in dict.fromkeys(m.group(1) for m in _UNSUPPORTED_ANCHOR.finditer(front)):
        findings.append(Finding(
            "warning", f"unsupported anchor type '{kind}:' is ignored — the "
            "parser only extracts path/pattern anchors, so this gives no "
            "protection; use a path or pattern anchor instead"))
    if scar.symbol_anchors:
        findings.append(Finding(
            "warning", "symbol anchor resolution pending — parsed but not yet "
            "enforced by the matcher; keep a path or pattern anchor as backup"))
    for pat in scar.pattern_anchors:
        try:
            re.compile(pat)
        except re.error as exc:
            findings.append(Finding("error", f"invalid pattern anchor /{pat}/: {exc}"))
            continue
        if _is_redos_prone(pat):
            findings.append(Finding(
                "error", f"pathological pattern anchor /{pat}/: nested quantifier "
                "risks catastrophic backtracking (ReDoS) — simplify the regex"))
    if not scar.evidence:
        findings.append(Finding("warning", "no evidence links — challengeable on sight"))
    for e in scar.evidence:
        if e.startswith("url:") and not is_valid_url(e[len("url:"):]):
            value = e[len("url:"):].strip()
            findings.append(Finding(
                "warning", f"url evidence not a valid http(s) link: '{value}'"))
    # ISO dates compare correctly as strings; never an error — a human
    # decides whether to archive (ADR-4), lint only surfaces the due date
    if (scar.status in ("active", "challenged") and scar.review_after
            and scar.review_after < today):
        findings.append(Finding(
            "warning", f"review_after {scar.review_after} is past — re-verify "
            "the scar still holds, then update the date or archive it"))
    if not scar.body:
        findings.append(Finding("warning", "empty body — future readers get no why"))
    return findings
