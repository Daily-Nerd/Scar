"""Scar model: parse, validate shape, serialize. One parser for the whole system."""

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
