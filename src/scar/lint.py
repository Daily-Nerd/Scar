"""Lint rules. Each rule exists because the failure already happened once:
no-frontmatter = daimon 0004 (born unable to fire); the rest harden the
contract in .scars/README.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import clock, presence, symbols
from .config import Config, signing_problem
from .model import SEVERITIES, STATUSES, TYPES, ParseError, Scar, is_valid_url, parse_scar_text
from .record import MAX_RECORD_BYTES, MAX_RECORD_LINES, RenderFit, record_hash


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


# #404: the reader takes regex fields verbatim (no YAML unescaping), so a
# doubled backslash before a regex escape or metacharacter is a literal
# backslash followed by that character. It compiles and is not ReDoS-prone.
# Usually the author meant the escape, but a target that really holds a
# backslash (regex source in a raw string) is matched by the doubled form, so
# the check only claims what the spelling proves.
_OVERESCAPE_NEXT = frozenset("()[]{}.*+?|^$bBsSwWdD")


def _overescape_runs(pattern: str) -> list[tuple[int, int]]:
    """(start, length) of each over-escaped backslash run in *pattern*.

    A backslash run is read left to right: an odd run ends in a real escape
    (the last backslash binds the next character) and is never flagged; an
    even run is only literal backslashes, so the character after it is
    unescaped. An even run of two or more directly before a character in
    _OVERESCAPE_NEXT is flagged, which covers four backslashes too: they
    mean two literal backslashes and then a live metacharacter, almost never
    intended. A run at the very end of the string, or before an ordinary
    character, is a legitimate literal backslash and is not flagged.

    Runs inside a character class are skipped, structurally: `[\\\\]` is the
    sanctioned way to say "a literal backslash" and must stay clean, so the
    escape hatch is the class itself, not a string match on its spelling.
    """
    runs: list[tuple[int, int]] = []
    in_class = False
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\":
            j = i
            while j < n and pattern[j] == "\\":
                j += 1
            length = j - i
            if (not in_class and length % 2 == 0 and j < n
                    and pattern[j] in _OVERESCAPE_NEXT):
                runs.append((i, length))
            # Odd run: the last backslash escapes pattern[j], so it can
            # neither open nor close a class. Even run: pattern[j] is live.
            i = j + 1 if length % 2 else j
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
            # A leading "^" and then "]" are literal members of the class.
            if i + 1 < n and pattern[i + 1] == "^":
                i += 1
            if i + 1 < n and pattern[i + 1] == "]":
                i += 1
        i += 1
    return runs


def _overescape_respellings(pattern: str) -> tuple[str, str] | None:
    """(halved, class form) for an over-escaped *pattern*, or None when clean.

    Both rewrite only the flagged runs. The halved form is for an author who
    meant the escape. The class form puts each doubled pair in `[\\\\]`, for
    an author who meant a real backslash: it matches the same text as the
    original, and the class is the spelling the rule accepts.
    """
    runs = _overescape_runs(pattern)
    if not runs:
        return None
    halved, classed, last = [], [], 0
    for start, length in runs:
        gap = pattern[last:start]
        halved.append(gap)
        classed.append(gap)
        halved.append("\\" * (length // 2))
        classed.append("[\\\\]" * (length // 2))
        last = start + length
    tail = pattern[last:]
    halved.append(tail)
    classed.append(tail)
    return "".join(halved), "".join(classed)


def _regex_field_findings(label: str, value: str) -> tuple[list[Finding], re.Pattern | None]:
    """The one gate every regex-valued field passes (#404): pattern anchors,
    command anchors, violation and revives_if. Compile, over-escape, ReDoS.

    Returns the findings and the compiled regex (None when it did not
    compile), so a caller with a field-specific rule can reuse it.
    """
    findings = []
    # Reported even when the regex does not compile: `NewConformance\\(` fails
    # with "missing )", which hides the real cause. Name both.
    respelled = _overescape_respellings(value)
    if respelled is not None:
        halved, classed = respelled
        findings.append(Finding(
            "error", f"over-escaped {label} /{value}/: a doubled backslash "
            "before a regex escape or metacharacter matches a literal "
            "backslash followed by that character, not the regex escape (the "
            "value is read verbatim, nothing un-escapes it). If you meant "
            f"the escape, write /{halved}/. If the text really contains a "
            f"backslash, write /{classed}/"))
    try:
        rx = re.compile(value)
    except re.error as exc:
        findings.append(Finding("error", f"invalid {label} /{value}/: {exc}"))
        return findings, None
    if _is_redos_prone(value):
        findings.append(Finding(
            "error", f"pathological {label} /{value}/: nested quantifier "
            "risks catastrophic backtracking (ReDoS) — simplify the regex"))
    return findings, rx


# The model extracts `path`/`pattern`/`symbol` anchors; any other anchor key is
# silently dropped, so a SPEC-following author who writes `- touch:` or
# `- breaks:` gets false protection (the scar never fires on that anchor).
# Scan the raw frontmatter and warn — but only warn, never error: flagging these
# as errors would break existing repos that already carry such anchors.
# `symbol:` anchors ARE parsed into Scar.symbol_anchors and, when the
# [symbols] extra is installed, ARE enforced by the matcher — see the
# availability-aware warning below, which is separate from this
# unsupported-anchor warning.
# #344: widened from touch/breaks to any kind the parser does not read, but only
# inside the anchors: block, so `- note:` under evidence: is never flagged.
_ANCHORS_BLOCK = re.compile(r"^anchors:[^\n]*\n((?:[ \t]+[^\n]*\n|[ \t]*\n)*)",
                            re.MULTILINE)
_ANCHOR_KIND_LINE = re.compile(r"^\s*-\s*([a-z_]+):", re.MULTILINE)
_SUPPORTED_ANCHOR_KINDS = ("path", "pattern", "symbol", "command")

# #344: `anchors: [...]` on the key line. The parser reads one `- kind: value`
# line per anchor, so a flow-style list loads with zero anchors. A trailing
# comment is not a value.
_FLOW_ANCHORS = re.compile(r"^anchors:[ \t]*[^\s#]", re.MULTILINE)

# Markdown link syntax or a bare URL in a title is machine leakage from a
# harvester, never something a human reviewer wrote as prose (#159).
_MARKDOWN_IN_TITLE = re.compile(r"\[[^\]]*\]\([^)]*\)|https?://")


@dataclass
class Finding:
    level: str  # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.message}"


# `authors` records the runtime that DRAFTED a candidate. The template carries
# an angle-bracket placeholder instead of a default, so a copy nobody edited
# fails here rather than silently crediting the wrong runtime. Role words name
# no runtime, and the human reviewer belongs in promoted_by (written by
# promote), never in authors. Checked on candidates only: that is the write
# gate, and promoted scars keep whatever history they were promoted with.
_ROLE_WORDS = frozenset({"agent", "assistant", "ai", "maintainer", "human",
                         "user", "reviewer", "owner"})


def _candidate_author_findings(authors: list[str]) -> list[Finding]:
    findings = []
    for author in authors:
        value = author.strip()
        if re.fullmatch(r"<[^<>]*>", value):
            findings.append(Finding(
                "error", f"authors still holds the template placeholder "
                f"{value}: replace it with the runtime that drafted this "
                "(e.g. claude-code, codex, cursor)"))
        elif value.casefold() in _ROLE_WORDS:
            findings.append(Finding(
                "error", f"authors names a role, not a runtime: '{value}'. "
                "Use the drafting runtime (e.g. claude-code); the human "
                "reviewer goes in promoted_by, written by `scar promote`"))
    return findings


CONFIG_REL = ".scars/config.json"


def downgrade_findings(scars: list[tuple[str, Scar]], cfg: Config,
                       next_id: int) -> list[tuple[str, Finding]]:
    """The downgrade guard (#392): structural, so CI runs it with no verifier.
    `scars` are the numbered scar files (candidates excluded) as (relative
    path, scar). Signing is switched off by editing files, not by forging an
    act, so lint refuses both ways of doing it:
    - an act with no org in the config (the org was deleted);
    - an org, and a scar at or after signed_from with no act (the act was
      dropped, or the scar was promoted around signing).
    - an act whose inputs_hash is not the hash of the scar's current record
      (#409): the record was changed after signing. Only a mismatch is
      reported; a match proves nothing about the signature.
    It reads no signature: whether an act is genuine is the checker's job."""
    out: list[tuple[str, Finding]] = []
    if cfg.org is None:
        for rel, scar in scars:
            if scar.promotion_act:
                out.append((rel, Finding(
                    "error", "carries promotion_act but .scars/config.json names no org; "
                    "signing was switched off, or the org was removed to skip it")))
        return out
    problem = signing_problem(cfg, next_id)
    if problem:
        out.append((CONFIG_REL, Finding("error", problem)))
    # With no org (returned above) the act is already an error for that cause,
    # so staleness is only judged once an org exists: one cause, one error.
    for rel, scar in scars:
        if not scar.promotion_act or scar.id is None:
            continue
        try:
            signed = presence.act_inputs_hash(scar.promotion_act)
        except presence.MalformedAct as exc:
            out.append((rel, Finding(
                "error", f"scar {scar.id} carries a promotion_act that cannot be read "
                f"({exc.reason}); it cannot be compared with the record")))
            continue
        if signed != record_hash(scar):
            out.append((rel, Finding(
                "error", f"scar {scar.id} changed since it was signed: its promotion_act "
                "was made for a different record (id, type, title, severity, promoted_from, "
                "anchors or body), so it needs a new signature")))
    if cfg.signed_from is not None:
        for rel, scar in scars:
            if (scar.status != "candidate" and scar.id is not None
                    and scar.id >= cfg.signed_from and not scar.promotion_act):
                out.append((rel, Finding(
                    "error", f"scar {scar.id} was promoted under the org "
                    f"(signed_from is {cfg.signed_from}) but carries no promotion_act")))
    return out


def render_fit_findings(name: str, fit: RenderFit) -> list[Finding]:
    """The warning for a candidate whose record the signer would refuse (#419).
    A warning, never an error: a repo with signing off loses nothing, and the
    signer's own render check stays the authority at promotion."""
    if fit.fits:
        return []
    parts = []
    if fit.bytes_over:
        parts.append(f"{fit.bytes_over} bytes over the signer's limit of "
                     f"{MAX_RECORD_BYTES} ({fit.bytes} bytes)")
    if fit.lines_over:
        parts.append(f"{fit.lines_over} lines over the signer's limit of "
                     f"{MAX_RECORD_LINES} ({fit.lines} lines)")
    return [Finding(
        "warning", f"candidate {name}: record is {' and '.join(parts)}; it "
        "cannot be signed at promotion until it is shortened")]


def lint_text(text: str, today: str | None = None) -> list[Finding]:
    try:
        scar = parse_scar_text(text)
    except ParseError:
        return [Finding("error", "missing YAML frontmatter — this scar can NEVER fire")]
    today = today or clock.today()

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
    if scar.status == "candidate" and scar.promoted_from:
        findings.append(Finding(
            "warning", f"candidate carries promoted_from: {scar.promoted_from} — "
            "only promote writes this field; a candidate has not been promoted"))
    if scar.promoted_by_source == "git-config":
        # #295: git-config is a computed identity, not a typed vouch: an
        # agent running promote under the human's git identity produces this
        # exact value with nobody having actually looked. Warning, not
        # error: the promotion still stands, this just asks for a second look.
        # #308: say what is UNKNOWN, not what did not happen. This same value
        # is on every scar promoted before interactivity was recorded, and a
        # human may well have typed nothing while sitting right there. An
        # interactive promotion now records git-config-interactive instead.
        findings.append(Finding(
            "warning", f"promoted_by_source: git-config: {scar.promoted_by} "
            "was promoted under a git identity, with no reviewer typed and "
            "no interactive terminal recorded; confirm a human reviewed it"))
    front = text.split("\n---", 1)[0]
    if _FLOW_ANCHORS.search(front):
        findings.append(Finding(
            "error", "anchors: is a flow-style list; the parser reads one "
            '"- kind: value" line per anchor, so nothing is anchored; '
            "rewrite it as a block list"))
    elif (not scar.path_anchors and not scar.pattern_anchors
            and not scar.symbol_anchors and not scar.command_anchors):
        findings.append(Finding("error", "no anchors — scar protects nothing"))
    # Scan the anchors block for kinds the parser does not read.
    block = _ANCHORS_BLOCK.search(front)
    kinds = (m.group(1) for m in _ANCHOR_KIND_LINE.finditer(block.group(1))) if block else ()
    for kind in dict.fromkeys(k for k in kinds if k not in _SUPPORTED_ANCHOR_KINDS):
        findings.append(Finding(
            "warning", f"unsupported anchor type '{kind}:' is ignored, so "
            "this gives no protection; the supported kinds are path, "
            "pattern, symbol and command"))
    if scar.symbol_anchors and not symbols.symbols_available():
        findings.append(Finding(
            "warning", "symbol anchors need the [symbols] extra to resolve — "
            "install scar-cli[symbols], or keep a path/pattern anchor as backup"))
    for pat in scar.pattern_anchors:
        findings.extend(_regex_field_findings("pattern anchor", pat)[0])
    for cmd in scar.command_anchors:
        cmd_findings, rx = _regex_field_findings("command anchor", cmd)
        findings.extend(cmd_findings)
        if rx is not None and rx.search(""):
            findings.append(Finding(
                "error", f"command anchor /{cmd}/ matches the empty string — it "
                "would fire on every command; anchor a concrete command shape"))
    if scar.violation:
        findings.extend(_regex_field_findings("violation", scar.violation)[0])
    # revives_if (#205): compiled and matched against tracked content on every
    # liveness pass, so it carries the same gates as violation. NOT checked for
    # liveness: a revival predicate matching nothing is the HEALTHY state (the
    # hazard has not returned) — the #156 reintroduction-guard distinction.
    if scar.revives_if:
        findings.extend(_regex_field_findings("revives_if", scar.revives_if)[0])
    for name in scar.unreadable_fields:
        findings.append(Finding(
            "error", f"{name} is not a well-formed flow list (expected "
            f'[\"a\", \"b\"] on one line): it was read as empty'))
    if scar.status == "candidate":
        findings.extend(_candidate_author_findings(scar.authors))
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
