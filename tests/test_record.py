"""promoted_record: the one producer of the bytes a promotion signs (#382)."""
import pytest

from scar.model import parse_scar_text
from scar.record import promoted_record, record_hash

BASE = """---
id: 5
type: fence
title: Keep the odd retry loop
severity: high
confidence: 0.7
created: 2026-11-01
authors: ["kibukx"]
anchors:
  - path: src/x.py
  - pattern: foo\\s+bar
  - symbol: x.retry
  - command: git\\s+push
evidence:
  - note: first
promoted_by: kibukx
review_after: 2027-01-01
status: active
---

Line one.
Line two.
"""


def rec(text: str) -> bytes:
    return promoted_record(parse_scar_text(text))


def test_same_scar_twice_identical():
    s = parse_scar_text(BASE)
    assert promoted_record(s) == promoted_record(s)


def test_crlf_and_lf_identical():
    assert rec(BASE.replace("\n", "\r\n")) == rec(BASE)


@pytest.mark.parametrize("old,new", [
    ("status: active", "status: archived"),
    ("confidence: 0.7", "confidence: 0.1"),
    ("review_after: 2027-01-01", "review_after: 2030-05-05"),
    ("  - note: first", "  - note: second\n  - pr: 9"),
    ("promoted_by: kibukx", "promoted_by: someone"),
    ("status: active", "status: active\nviolation: foo"),
    ("created: 2026-11-01", "created: 2020-01-01"),
    ("status: active", "status: active\npromotion_act: a.b.c"),
    ("status: active", "status: active\npromotion_receipt: uEiAbc"),
])
def test_lifecycle_changes_keep_bytes(old, new):
    assert old in BASE
    assert rec(BASE.replace(old, new)) == rec(BASE)


@pytest.mark.parametrize("old,new", [
    ("id: 5", "id: 6"),
    ("type: fence", "type: landmine"),
    ("title: Keep the odd retry loop", "title: Other"),
    ("severity: high", "severity: low"),
    ("  - path: src/x.py", "  - path: src/y.py"),
    ("  - pattern: foo\\s+bar", "  - pattern: foo\\s+baz"),
    ("  - symbol: x.retry", "  - symbol: x.other"),
    ("  - command: git\\s+push", "  - command: git\\s+pull"),
    ("Line two.", "Line three."),
])
def test_decision_changes_alter_bytes(old, new):
    assert old in BASE
    assert rec(BASE.replace(old, new)) != rec(BASE)


def test_key_order_and_anchor_quote_style_do_not_matter():
    shuffled = """---
status: active
severity: 'high'
title: "Keep the odd retry loop"
type: "fence"
id: 5
anchors:
  - path: "src/x.py"
  - pattern: "foo\\s+bar"
  - symbol: x.retry
  - command: "git\\s+push"
---

Line one.
Line two.
"""
    assert rec(shuffled) == rec(BASE)


def test_mixed_anchor_kinds_are_grouped_in_fixed_order():
    text = """---
id: 1
type: deadend
title: t
severity: low
anchors:
  - command: c1
  - symbol: s1
  - pattern: p1
  - path: a1
  - command: c2
  - path: a2
---
b
"""
    assert rec(text) == (
        b"id: 1\ntype: deadend\ntitle: t\nseverity: low\nanchors:\n"
        b"  - path: a1\n  - path: a2\n  - pattern: p1\n  - symbol: s1\n"
        b"  - command: c1\n  - command: c2\n---\nb\n"
    )


def test_no_anchors_leaves_bare_anchors_line():
    text = "---\nid: 2\ntype: fence\ntitle: t\nseverity: low\n---\nb\n"
    assert rec(text) == b"id: 2\ntype: fence\ntitle: t\nseverity: low\nanchors:\n---\nb\n"


def test_golden_bytes():
    assert rec(BASE) == (
        b"id: 5\n"
        b"type: fence\n"
        b"title: Keep the odd retry loop\n"
        b"severity: high\n"
        b"anchors:\n"
        b"  - path: src/x.py\n"
        b"  - pattern: foo\\s+bar\n"
        b"  - symbol: x.retry\n"
        b"  - command: git\\s+push\n"
        b"---\n"
        b"Line one.\n"
        b"Line two.\n"
    )


def test_body_trailing_blank_lines_and_cr_normalized():
    text = BASE.replace("Line two.\n", "Line two.\r\n\r\n  \n\n")
    assert rec(text) == rec(BASE)


def test_unicode_is_utf8():
    text = BASE.replace("Line one.", "Línea ñ ✓")
    assert "Línea ñ ✓".encode("utf-8") in rec(text)


def test_id_none_raises():
    scar = parse_scar_text(BASE.replace("id: 5\n", ""))
    with pytest.raises(ValueError, match="id"):
        promoted_record(scar)


def test_promoted_from_is_in_the_record_only_when_present():
    """Signed bytes of scars promoted before the field existed must not move."""
    assert b"promoted_from" not in rec(BASE)
    with_from = rec(BASE.replace("promoted_by: kibukx",
                                 "promoted_by: kibukx\npromoted_from: candidates/x.md"))
    assert b"promoted_from: candidates/x.md\n" in with_from
    assert with_from != rec(BASE)


def test_record_hash_is_a_prefixed_multihash_of_the_record_bytes():
    import base64
    import hashlib
    digest = hashlib.sha256(promoted_record(parse_scar_text(BASE))).digest()
    expected = "u" + base64.urlsafe_b64encode(b"\x12\x20" + digest).decode().rstrip("=")
    assert record_hash(parse_scar_text(BASE)) == expected
    assert expected.startswith("uEi") and "=" not in expected


def test_record_hash_follows_the_record_and_ignores_lifecycle():
    scar = parse_scar_text(BASE)
    before = record_hash(scar)
    scar.status = "archived"
    scar.evidence.append("note: more")
    assert record_hash(scar) == before
    scar.title = "Another title"
    assert record_hash(scar) != before


def test_the_conformance_fixture_act_pins_scars_hash_to_the_signers():
    import json
    import conformance_support as cs
    from scar import presence
    fixture = json.loads(cs.FIXTURE_PATH.read_text())
    scar = parse_scar_text(cs.FIXTURE_SCAR)
    assert presence.act_inputs_hash(fixture["jws"]) == record_hash(scar)
