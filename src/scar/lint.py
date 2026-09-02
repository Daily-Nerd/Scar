"""Lint rules. Each rule exists because the failure already happened once:
no-frontmatter = daimon 0004 (born unable to fire); the rest harden the
contract in .scars/README.md.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import symbols
from .model import SEVERITIES, STATUSES, TYPES, ParseError, is_valid_url, parse_scar_text


# A valid regex can still be a ReDoS weapon. re.compile accepts it (the syntax
# is fine) so a plain compile-check lets it through; search() then hangs on
# adversarial input on the read hot path. Two catastrophic families are gated
# here (#184 — the old single-regex check missed both beyond one nesting level):
#   1. A quantified group whose INTERIOR contains a quantifier at any depth:
#      (a+)+, ((a+))+, (x(y+)z)*, ([a-z]+)*.
#   2. A quantified alternation with identical or prefix-overlapping branches:
#      (a|a)*, (x|xy)*, (?:foo|foobar)+.
# Deliberately over-broad on family 1 (e.g. (ab+c)* is flagged even when the
# surrounding literals make it deterministic) — this is a promote/CI gate with
# a human in the loop, and the pre-#184 gate had the same conservatism one
# level shallower. Escapes and character classes are skipped, so redis\.get\(
# and [+*]+ never trip it.
_GROUP_PREFIX = re.compile(r"\?(?:P<[^>]*>|<[=!]|[:=!])")


def _scan_groups(pattern: str) -> list[tuple[int, int]]:
    """(start, end) index pairs of parenthesized groups, end pointing at ')'.
    Balanced scan skipping escaped chars and [...] classes."""
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    in_class = False
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "(":
            stack.append(i)
        elif c == ")" and stack:
            spans.append((stack.pop(), i))
        i += 1
    return spans


def _strip_group_prefix(interior: str) -> str:
    """Drop a leading (?: / (?= / (?! / (?<= / (?<! / (?P<name> marker."""
    m = _GROUP_PREFIX.match(interior)
    return interior[m.end():] if m else interior


def _has_bare_quantifier(text: str) -> bool:
    """True iff text contains an unescaped '*' or '+' outside a [...] class."""
    in_class = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c in "*+":
            return True
        i += 1
    return False


def _top_level_branches(text: str) -> list[str]:
    """Split on '|' at depth 0, outside classes, ignoring escaped bars."""
    branches: list[str] = []
    cur: list[str] = []
    depth = 0
    in_class = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            cur.append(text[i:i + 2])
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif c == "|" and depth == 0:
            branches.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    branches.append("".join(cur))
    return branches


def _is_redos_prone(pattern: str) -> bool:
    for start, end in _scan_groups(pattern):
        if end + 1 >= len(pattern) or pattern[end + 1] not in "*+":
            continue
        interior = _strip_group_prefix(pattern[start + 1:end])
        if _has_bare_quantifier(interior):
            return True
        branches = _top_level_branches(interior)
        if len(branches) > 1:
            for i, a in enumerate(branches):
                for b in branches[i + 1:]:
                    if a and b and (a.startswith(b) or b.startswith(a)):
                        return True
    return False


# The model extracts `path`/`pattern`/`symbol` anchors; any other anchor key is
# silently dropped, so a SPEC-following author who writes `- touch:` or
# `- breaks:` gets false protection (the scar never fires on that anchor).
# Scan the raw frontmatter and warn — but only warn, never error: flagging these
# as errors would break existing repos that already carry such anchors.
# `symbol:` anchors ARE parsed into Scar.symbol_anchors and, when the
# [symbols] extra is installed, ARE enforced by the matcher — see the
# availability-aware warning below, which is separate from this
# unsupported-anchor warning.
_UNSUPPORTED_ANCHOR = re.compile(
    r"^\s*-\s*(touch|breaks):", re.MULTILINE)

# Markdown link syntax or a bare URL in a title is machine leakage from a
# harvester, never something a human reviewer wrote as prose (#159).
_MARKDOWN_IN_TITLE = re.compile(r"\[[^\]]*\]\([^)]*\)|https?://")


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
    elif _MARKDOWN_IN_TITLE.search(scar.title):
        findings.append(Finding(
            "warning", "title carries a markdown link or bare URL — harvester "
            "leakage, not prose; sanitize the title (and slug) before writing"))
    if scar.severity not in SEVERITIES:
        findings.append(Finding("error", f"invalid severity '{scar.severity}'"))
    if scar.status not in STATUSES:
        findings.append(Finding("error", f"invalid status '{scar.status}'"))
    elif not re.search(r"^\s*status:", text.split("\n---", 1)[0], re.MULTILINE):
        # #199: parse defaults an absent status to 'active', so a scar written
        # without the field is armed immediately with no reviewer recorded —
        # the promotion gate bypassed by omission. Presence is mandatory.
        findings.append(Finding(
            "error", "missing status field — an omitted status silently "
            "defaults to active, bypassing human promotion; write "
            "status: candidate (or the intended status) explicitly"))
    if scar.status == "candidate" and scar.promoted_by:
        # #287: only promote writes this, and a candidate has not been
        # promoted, so the field here is a claim nobody made (an agent can
        # write any name). Promote overwrites it regardless; this just makes
        # the pre-seeding visible instead of silent.
        findings.append(Finding(
            "warning", f"candidate carries promoted_by: {scar.promoted_by} — "
            "only promote writes this field; nobody has vouched for a candidate"))
    if (not scar.path_anchors and not scar.pattern_anchors
            and not scar.symbol_anchors and not scar.command_anchors):
        findings.append(Finding("error", "no anchors — scar protects nothing"))
    # Scan the raw frontmatter for anchor keys the model cannot represent.
    front = text.split("\n---", 1)[0]
    for kind in dict.fromkeys(m.group(1) for m in _UNSUPPORTED_ANCHOR.finditer(front)):
        findings.append(Finding(
            "warning", f"unsupported anchor type '{kind}:' is ignored — the "
            "parser only extracts path/pattern anchors, so this gives no "
            "protection; use a path or pattern anchor instead"))
    if scar.symbol_anchors and not symbols.symbols_available():
        findings.append(Finding(
            "warning", "symbol anchors need the [symbols] extra to resolve — "
            "install scar-cli[symbols], or keep a path/pattern anchor as backup"))
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
    for cmd in scar.command_anchors:
        try:
            rx = re.compile(cmd)
        except re.error as exc:
            findings.append(Finding("error", f"invalid command anchor /{cmd}/: {exc}"))
            continue
        if rx.search(""):
            findings.append(Finding(
                "error", f"command anchor /{cmd}/ matches the empty string — it "
                "would fire on every command; anchor a concrete command shape"))
        if _is_redos_prone(cmd):
            findings.append(Finding(
                "error", f"pathological command anchor /{cmd}/: nested quantifier "
                "risks catastrophic backtracking (ReDoS) — simplify the regex"))
    if scar.violation:
        try:
            re.compile(scar.violation)
        except re.error as exc:
            findings.append(Finding("error", f"invalid violation /{scar.violation}/: {exc}"))
        else:
            if _is_redos_prone(scar.violation):
                findings.append(Finding(
                    "error", f"pathological violation /{scar.violation}/: nested quantifier "
                    "risks catastrophic backtracking (ReDoS) — simplify the regex"))
    # revives_if (#205): compiled and matched against tracked content on every
    # liveness pass, so it carries the same gates as violation. NOT checked for
    # liveness: a revival predicate matching nothing is the HEALTHY state (the
    # hazard has not returned) — the #156 reintroduction-guard distinction.
    if scar.revives_if:
        try:
            re.compile(scar.revives_if)
        except re.error as exc:
            findings.append(Finding(
                "error", f"invalid revives_if /{scar.revives_if}/: {exc}"))
        else:
            if _is_redos_prone(scar.revives_if):
                findings.append(Finding(
                    "error", f"pathological revives_if /{scar.revives_if}/: nested "
                    "quantifier risks catastrophic backtracking (ReDoS) — simplify "
                    "the regex"))
    # Author identity drift (#182): one handle, two casings = one human
    # credited twice. Warning, never error — two genuinely distinct people
    # whose handles differ only by case is possible, so report, don't break.
    folds: dict[str, set[str]] = {}
    for author in scar.authors:
        folds.setdefault(author.casefold(), set()).add(author)
    for spellings in folds.values():
        if len(spellings) > 1:
            findings.append(Finding(
                "warning", "authors differ only by case: "
                + ", ".join(sorted(spellings))
                + " — one human, two spellings; keep one"))
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
