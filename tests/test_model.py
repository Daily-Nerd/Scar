"""Scar model: parse, validate shape, serialize. One parser for the whole system."""

import re

import pytest

from scar.model import ParseError, Scar, parse_scar_text

VALID = """\
---
id: 7
type: deadend
title: Redis sessions failed
severity: high
confidence: 0.9
created: 2024-03-12
authors: ["claude-code", mara]
anchors:
  - path: services/auth/
  - pattern: "redis|aioredis"
evidence:
  - commit: a3f9c21
expires:
  condition: "sessions become re-derivable"
  review_after: 2027-03-12
status: active
---

Body prose here.
"""


def test_parses_valid_scar():
    s = parse_scar_text(VALID)
    assert s.id == 7
    assert s.type == "deadend"
    assert s.title == "Redis sessions failed"
    assert s.severity == "high"
    assert s.confidence == 0.9
    assert s.status == "active"
    assert s.path_anchors == ["services/auth/"]
    assert s.pattern_anchors == ["redis|aioredis"]
    assert s.body.startswith("Body prose")


def test_missing_frontmatter_raises():
    with pytest.raises(ParseError):
        parse_scar_text("# just markdown\n\nno frontmatter\n")


def test_status_defaults_to_active():
    text = VALID.replace("status: active\n", "")
    assert parse_scar_text(text).status == "active"


def test_confidence_defaults_when_malformed():
    text = VALID.replace("confidence: 0.9", "confidence: not-a-number")
    assert parse_scar_text(text).confidence == 0.5


def test_inline_comment_stripped_from_type():
    # The template ships `type: deadend  # ...`; a copied-but-uncleaned comment
    # must not become part of the value (else the type fails its enum check).
    text = VALID.replace("type: deadend", "type: deadend            # tried+failed")
    assert parse_scar_text(text).type == "deadend"


def test_inline_comment_stripped_from_confidence_keeps_value():
    # Silent-corruption case: a comment used to make float() fail -> reset to 0.5.
    text = VALID.replace("confidence: 0.9", "confidence: 0.9            # 0..1 how sure")
    assert parse_scar_text(text).confidence == 0.9


def test_inline_comment_stripped_from_severity():
    text = VALID.replace("severity: high", "severity: high            # low | medium | high | critical")
    assert parse_scar_text(text).severity == "high"


def test_quoted_hash_is_data_not_comment():
    # A '#' inside a quoted scalar is part of the value, not a comment.
    text = VALID.replace('condition: "sessions become re-derivable"',
                         'condition: "drop #legacy sessions"')
    assert parse_scar_text(text).expires_condition == "drop #legacy sessions"


def test_quoted_pattern_anchor_unwrapped():
    s = parse_scar_text(VALID)
    assert '"' not in s.pattern_anchors[0]


def test_roundtrip_preserves_fields():
    s = parse_scar_text(VALID)
    s2 = parse_scar_text(s.to_text())
    assert (s2.id, s2.type, s2.title, s2.status) == (7, "deadend", "Redis sessions failed", "active")
    assert s2.path_anchors == s.path_anchors
    assert s2.body == s.body
    assert s2.expires_condition == s.expires_condition
    assert s2.review_after == s.review_after
    assert s2.evidence == s.evidence


def test_symbol_anchor_parses_and_roundtrips():
    from scar.model import Scar, parse_scar_text
    s = Scar(title="t", path_anchors=["src/store.py"], symbol_anchors=["SessionStore", "src/store.py::SessionStore.save"])
    s2 = parse_scar_text(s.to_text())
    assert s2.symbol_anchors == ["SessionStore", "src/store.py::SessionStore.save"]
    assert s2.path_anchors == ["src/store.py"]


def test_symbol_anchor_parsed_from_raw_frontmatter():
    from scar.model import parse_scar_text
    text = (
        "---\ntype: deadend\ntitle: t\nseverity: medium\nconfidence: 0.5\n"
        "anchors:\n  - path: src/store.py\n  - symbol: SessionStore\nstatus: active\n---\nbody\n"
    )
    s = parse_scar_text(text)
    assert s.symbol_anchors == ["SessionStore"]


def test_to_text_can_override_status_and_id():
    s = parse_scar_text(VALID)
    s.id, s.status = 12, "candidate"
    s2 = parse_scar_text(s.to_text())
    assert s2.id == 12 and s2.status == "candidate"


def test_evidence_with_inner_quotes_roundtrips():
    text = """\
---
type: landmine
title: Test with quotes
severity: high
confidence: 0.9
status: active
evidence:
  - note: "This has \"inner\" quotes"
---

Body.
"""
    s = parse_scar_text(text)
    assert len(s.evidence) == 1
    assert 'inner' in s.evidence[0]
    s2 = parse_scar_text(s.to_text())
    assert s2.evidence == s.evidence


def test_evidence_inline_comment_stripped(  # noqa: D103
):
    """An inline comment after an evidence value must not become part of the
    value (#144): `- commit: e665a43  # why` silently voided the receipt —
    _commit_shas' SHA regex failed and the unreachable check skipped the scar.
    The template itself ships evidence lines with inline comments, so this is
    the documented style, not an edge case. Same #69 semantics as scalar
    fields: unquoted values strip the comment, quoted values keep their '#'."""
    text = """\
---
type: landmine
title: Evidence comment footgun
severity: medium
confidence: 0.7
anchors:
  - path: src/scar/
evidence:
  - commit: e665a43  # why this sha matters
  - pr: 123                # at least one receipt: pr, issue, url, commit
  - note: "quoted # stays data"
status: active
---

Body.
"""
    s = parse_scar_text(text)
    assert s.evidence == [
        "commit: e665a43",
        "pr: 123",
        "note: quoted # stays data",
    ]


def test_issue_and_url_evidence_parse_and_roundtrip():
    text = """\
---
type: landmine
title: Durable evidence forms
severity: medium
confidence: 0.7
anchors:
  - path: src/scar/
evidence:
  - pr: 123
  - issue: 50
  - url: https://github.com/org/repo/commit/abc1234
status: active
---

Body.
"""
    s = parse_scar_text(text)
    assert s.evidence == [
        "pr: 123",
        "issue: 50",
        "url: https://github.com/org/repo/commit/abc1234",
    ]
    s2 = parse_scar_text(s.to_text())
    assert s2.evidence == s.evidence


def test_title_with_hash_survives_roundtrip():
    # Issue #91.1: a '#' in a free-text title (issue ref) must not be stripped
    # as an inline comment on round-trip (parse -> to_text -> parse).
    s = Scar(title="Fix #40 regression", type="deadend", severity="high",
             confidence=0.9, status="active", path_anchors=["src/"])
    s2 = parse_scar_text(s.to_text())
    assert s2.title == "Fix #40 regression"


def test_crlf_frontmatter_parses():
    # Issue #91.3: a scar with CRLF line endings must still parse.
    crlf = VALID.replace("\n", "\r\n")
    s = parse_scar_text(crlf)
    assert s.id == 7
    assert s.title == "Redis sessions failed"
    assert s.path_anchors == ["services/auth/"]


def test_path_anchor_with_space_captured():
    # Issue #91.4: a path anchor containing a space must be captured, not dropped.
    text = VALID.replace("  - path: services/auth/\n",
                         "  - path: src/my dir/\n")
    s = parse_scar_text(text)
    assert s.path_anchors == ["src/my dir/"]


def test_path_anchor_inline_comment_still_stripped():
    # Guard: widening the path capture must not re-admit the template's inline
    # comment into the anchor value.
    text = VALID.replace("  - path: services/auth/\n",
                         "  - path: services/auth/   # protects auth\n")
    s = parse_scar_text(text)
    assert s.path_anchors == ["services/auth/"]


def test_violation_field_parses_quoted():
    # Task 1: violation is an optional top-level frontmatter scalar — a
    # post-edit tripwire regex. Quoted form, mirroring expires.condition.
    text = VALID.replace(
        'expires:\n  condition: "sessions become re-derivable"\n  review_after: 2027-03-12\n',
        'expires:\n  condition: "sessions become re-derivable"\n  review_after: 2027-03-12\n'
        'violation: "shutil\\.which"\n',
    )
    s = parse_scar_text(text)
    assert s.violation == "shutil\\.which"


def test_violation_field_parses_unquoted():
    text = VALID.replace("status: active\n", "violation: shutil.which\nstatus: active\n")
    s = parse_scar_text(text)
    assert s.violation == "shutil.which"


def test_violation_defaults_empty_when_absent():
    s = parse_scar_text(VALID)
    assert s.violation == ""


def test_violation_roundtrip_via_to_text():
    s = Scar(title="t", type="deadend", severity="high", confidence=0.9,
             status="active", path_anchors=["src/"], violation="shutil\\.which")
    s2 = parse_scar_text(s.to_text())
    assert s2.violation == s.violation


def test_to_text_omits_violation_line_when_absent():
    s = Scar(title="t", type="deadend", severity="high", confidence=0.9,
             status="active", path_anchors=["src/"])
    assert "violation:" not in s.to_text()


def test_violation_with_embedded_double_quotes_roundtrips():
    # #200: a violation regex matching an HTML attribute (id="state") needs a
    # literal double quote. to_text() used to always wrap in double quotes,
    # producing `violation: "id="state""` -- unrecoverable on the next parse,
    # since nothing un-escapes the interior quotes and stripping only removes
    # the outermost quote characters. The stored regex silently stopped
    # matching what it was written for while `lint` still reported it clean.
    regex = 'id="state"[^>]*aria-live|aria-live[^>]*id="state"'
    s = Scar(title="t", type="fence", severity="high", confidence=0.9,
             status="active", path_anchors=["src/"], violation=regex)
    s2 = parse_scar_text(s.to_text())
    assert s2.violation == regex
    compiled = re.compile(s2.violation)
    assert compiled.search('<div id="state" aria-live="polite">')
    assert compiled.search('<div aria-live="polite" id="state">')


def test_violation_single_quoted_source_with_embedded_double_quotes_parses():
    # #200 Path B: an author works around the lack of escaping by wrapping the
    # value in single quotes so the interior double quotes stay literal. The
    # read side used to strip only double quotes, leaving the wrapper single
    # quotes attached -- the regex then began with a literal "'" and could
    # never match real source, even though `scar lint` reported 0 errors.
    text = VALID.replace(
        "status: active\n",
        "violation: 'id=\"state\"[^>]*aria-live|aria-live[^>]*id=\"state\"'\n"
        "status: active\n",
    )
    s = parse_scar_text(text)
    assert s.violation == 'id="state"[^>]*aria-live|aria-live[^>]*id="state"'
    compiled = re.compile(s.violation)
    assert compiled.search('<div id="state" aria-live="polite">')


def test_violation_with_edge_apostrophes_survives_roundtrip():
    # A regex whose content legitimately starts/ends with an apostrophe
    # (matching a single-quoted token like 'use client'). Wrapper removal must
    # take exactly one matching PAIR — char-wise stripping eats the data's own
    # apostrophes, silently broadening the regex, and every re-serialization
    # path persists the corruption.
    scar = Scar(title="t", violation="'use client'")
    again = parse_scar_text(scar.to_text())
    assert again.violation == "'use client'"


def test_violation_emission_stays_double_quoted():
    # .scars/ files are git-shared while scar binaries are per-machine: every
    # released parser removes only double-quote wrappers, so a single-quoted
    # emission reads back with wrapper apostrophes attached on older binaries
    # — regex compiles, lint is clean, tripwire silently dead. Emission must
    # stay double-quoted; _unquote's pair semantics make embedded double
    # quotes recoverable without a wrapper flip.
    scar = Scar(title="t", violation='id="state"')
    line = [ln for ln in scar.to_text().splitlines() if ln.startswith("violation:")][0]
    assert line == 'violation: "id="state""'


def test_existing_evidence_prefixes_still_parse():
    text = """\
---
type: deadend
title: keeps old forms
severity: low
confidence: 0.5
anchors:
  - path: a/
evidence:
  - commit: a3f9c21
  - incident: 2024-prod-outage
  - note: archived 2025-01-01: superseded
status: active
---

Body.
"""
    s = parse_scar_text(text)
    assert s.evidence == [
        "commit: a3f9c21",
        "incident: 2024-prod-outage",
        "note: archived 2025-01-01: superseded",
    ]


# --- command anchors (#175) ---

COMMAND_SCAR = """\
---
id: 4
type: deadend
title: Bare uv sync strips extras
severity: high
confidence: 0.9
created: 2026-07-30
authors: ["kib"]
anchors:
  - command: "uv sync(?!.* --all-extras)"
evidence:
  - issue: 175
status: active
---

Always run uv sync --all-extras.
"""


def test_parse_command_anchor():
    scar = parse_scar_text(COMMAND_SCAR)
    assert scar.command_anchors == ["uv sync(?!.* --all-extras)"]
    assert scar.path_anchors == []


def test_command_anchor_roundtrips_through_to_text():
    scar = parse_scar_text(COMMAND_SCAR)
    again = parse_scar_text(scar.to_text())
    assert again.command_anchors == ["uv sync(?!.* --all-extras)"]


# --- #202: quoted values must survive a write/read round-trip byte-identical.
# to_text() wraps pattern/command/condition in exactly one double-quote pair;
# parse must remove exactly that pair, never edge quote CHARACTERS that are
# part of the value.


def test_pattern_anchor_with_edge_quote_survives_roundtrip():
    scar = Scar(title="t", pattern_anchors=['class="'])
    again = parse_scar_text(scar.to_text())
    assert again.pattern_anchors == ['class="']


def test_command_anchor_with_embedded_quotes_survives_roundtrip():
    scar = Scar(title="t", command_anchors=['grep "foo"'])
    again = parse_scar_text(scar.to_text())
    assert again.command_anchors == ['grep "foo"']


def test_condition_with_edge_quote_survives_roundtrip():
    scar = Scar(title="t", expires_condition='flag="on"')
    again = parse_scar_text(scar.to_text())
    assert again.expires_condition == 'flag="on"'


def test_pattern_anchor_fully_quoted_value_survives_roundtrip():
    # A regex that itself matches a double-quoted token: the wrapper pair is
    # removed once, the value's own quotes stay.
    scar = Scar(title="t", pattern_anchors=['"redis"'])
    again = parse_scar_text(scar.to_text())
    assert again.pattern_anchors == ['"redis"']


def test_single_quoted_command_anchor_still_unwrapped():
    # daimon-0021 protection: a hand-written single-quoted command anchor must
    # not keep its wrapper (silently-dead tripwire).
    text = COMMAND_SCAR.replace(
        '- command: "uv sync(?!.* --all-extras)"',
        "- command: 'uv sync(?!.* --all-extras)'",
    )
    assert parse_scar_text(text).command_anchors == ["uv sync(?!.* --all-extras)"]


def test_quoted_pattern_with_trailing_comment_is_stripped():
    # The template ships trailing comments; a quoted value used to fuse the
    # comment into the regex (compiles, lints clean, never matches).
    text = VALID.replace(
        '- pattern: "redis|aioredis"',
        '- pattern: "redis|aioredis"  # tripwire',
    )
    assert parse_scar_text(text).pattern_anchors == ["redis|aioredis"]


def test_quoted_condition_with_trailing_comment_is_stripped():
    text = VALID.replace(
        'condition: "sessions become re-derivable"',
        'condition: "sessions become re-derivable"  # when to retire',
    )
    assert parse_scar_text(text).expires_condition == "sessions become re-derivable"


def test_quoted_command_with_trailing_comment_is_stripped():
    text = COMMAND_SCAR.replace(
        '- command: "uv sync(?!.* --all-extras)"',
        '- command: "uv sync(?!.* --all-extras)"  # regex',
    )
    assert parse_scar_text(text).command_anchors == ["uv sync(?!.* --all-extras)"]
